# v3 – GDS Itinerary → MTJ Converter with banner + NDC toggle

import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any

import streamlit as st

# Time patterns
TIME_WITH_AP = re.compile(r'^\d{3,4}[AP]$', re.IGNORECASE)      # 531P, 834P
TIME_PLAIN   = re.compile(r'^(\d{4})(\+1)?$', re.IGNORECASE)    # 1250, 1914, 1115+1

NDC_XML = (
    'XMLREQUEST <ExpediaRequest><ConfigurationVariables>'
    '<ConfigVar name="BFSQIBNDCEnabled" value="1" />'
    '<ConfigVar name="BFSQAFKLGroupNDCEnabled" value="1" />'
    '<ConfigVar name="BFSQHANDCEnabled" value="1" />'
    '<ConfigVar name="BFSQAANDCEnabled" value="1" />'
    '<ConfigVar name="BFSQBANDCEnabled" value="1" />'
    '<ConfigVar name="BFSQACNDCEnabled" value="1" />'
    '<ConfigVar name="BFSQA3NDCEnabled" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailSN" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailMiscCxrs" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailLX" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailKL" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailBA" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailAF" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailHA" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailAA" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailLH" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailAC" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailA3" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailOS" value="1" />'
    '<ConfigVar name="BFSQDirectConnectAvailIB" value="1" />'
    ' <ConfigVar name="BFSQLHGroupNDCEnabled" value="1" />'
    '<ConfigVar name="BFSQDownlevelControlMask" value="1" />'
    '</ConfigurationVariables><CRSDefaults><NDC>'
    '<CRS CRSID="40" CARRIERS="AF,KL,BA,IB,AC"/>'
    '<CRS CRSID="41" CARRIERS="AA,LH,OS,LX,SN,HA,A3"/>'
    '</NDC><SHOPPING><CRS CRSID="7" CARRIERS="$$"/></SHOPPING>'
    '</CRSDefaults></ExpediaRequest>'
)

def parse_time_12h(token: str) -> str:
    """Convert '531P' / '1048A' -> 'HH:MM' 24h."""
    token = token.strip().upper()
    ampm = token[-1]
    digits = token[:-1]
    if ampm not in ("A", "P") or not digits.isdigit():
        return "12:00"

    if len(digits) <= 2:
        hour = int(digits)
        minute = 0
    else:
        hour = int(digits[:-2])
        minute = int(digits[-2:])

    if ampm == "P" and hour != 12:
        hour += 12
    if ampm == "A" and hour == 12:
        hour = 0

    return f"{hour:02d}:{minute:02d}"

def parse_time_24h(token: str) -> str:
    """Convert '0723' -> '07:23'."""
    digits = token.strip()
    if not digits.isdigit() or len(digits) != 4:
        return "12:00"
    hour = int(digits[:2])
    minute = int(digits[2:])
    return f"{hour:02d}:{minute:02d}"

def parse_itinerary_lines(raw: str) -> List[str]:
    """Keep only segment lines (starting with a number)."""
    seg_lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if parts and parts[0].isdigit():
            seg_lines.append(line)
    return seg_lines

def parse_segments(raw: str, year: int) -> List[Dict[str, Any]]:
    """
    Parse GDS-style segments:
      - Combined carrier/flight: 1 UA2280 N 30APR 4 ORDMCO ...
      - Split carrier/flight:    2 AF 089 Y 15SEP 2 MSPCDG ...
    """
    seg_lines = parse_itinerary_lines(raw)
    segments: List[Dict[str, Any]] = []

    for line in seg_lines:
        parts = line.split()
        if len(parts) < 5:
            continue

        idx = parts[0]

        # Detect split vs combined carrier/flight
        if (
            len(parts) >= 5
            and parts[1].isalpha()
            and len(parts[1]) == 2
            and parts[2].isdigit()
        ):
            # Split: 2 AF 089 Y 15SEP 2 MSPCDG ...
            carrier = parts[1].upper()
            flight = parts[2]
            booking_class = parts[3]
            date_raw = parts[4]
            odpair_index = 6
        else:
            # Combined: 1 UA2280 N 30APR 4 ORDMCO ...
            cxr_flt = parts[1]
            carrier = cxr_flt[:2].upper()
            flight = cxr_flt[2:]
            booking_class = parts[2]
            date_raw = parts[3]
            odpair_index = 5

        od_pair = parts[odpair_index] if len(parts) > odpair_index else ""

        if len(od_pair) >= 6:
            orig = od_pair[:3].upper()
            dest = od_pair[3:6].upper()
        else:
            orig = ""
            dest = ""

        # Departure date
        try:
            dep_date = datetime.strptime(f"{date_raw.upper()}{year}", "%d%b%Y").date()
        except ValueError:
            dep_date = date(year, 1, 1)

        # Collect time tokens after od_pair
        time_tokens: List[Dict[str, Any]] = []
        for tok in parts[odpair_index + 1 :]:
            clean = tok.strip().upper()

            # 12h style: 531P
            if TIME_WITH_AP.fullmatch(clean):
                time_tokens.append(
                    {"raw": clean, "is_ampm": True, "plus1": False}
                )
                continue

            # 24h style: 0723, 1115+1
            m = TIME_PLAIN.fullmatch(clean)
            if m:
                time_tokens.append(
                    {
                        "raw": m.group(1),
                        "is_ampm": False,
                        "plus1": bool(m.group(2)),
                    }
                )

        if not time_tokens:
            dep_time = "12:00"
            arr_time = "12:00"
            arr_date = dep_date
        else:
            first = time_tokens[0]
            last = time_tokens[-1]

            if first["is_ampm"]:
                dep_time = parse_time_12h(first["raw"])
            else:
                dep_time = parse_time_24h(first["raw"])

            if last["is_ampm"]:
                arr_time = parse_time_12h(last["raw"])
            else:
                arr_time = parse_time_24h(last["raw"])

            arr_date = dep_date + (timedelta(days=1) if last["plus1"] else timedelta(0))

        segments.append(
            {
                "index": idx,
                "carrier": carrier,
                "flight": flight,
                "booking_class": booking_class,  # Z, Y, G, N...
                "orig": orig,
                "dest": dest,
                "dep_date": dep_date,
                "dep_time": dep_time,
                "arr_date": arr_date,
                "arr_time": arr_time,
            }
        )

    return segments

def split_bounds_by_date(segments: List[Dict[str, Any]]) -> (List[Dict[str, Any]], List[Dict[str, Any]]):
    """
    Heuristic:
      - If all segments share same dep_date => OW (outbound only).
      - If dep_date changes => RT: outbound = first date group, inbound = from first change.
    """
    if not segments:
        return [], []

    first_date = segments[0]["dep_date"]
    split_idx = len(segments)
    for i, seg in enumerate(segments):
        if seg["dep_date"] != first_date:
            split_idx = i
            break

    outbound = segments[:split_idx]
    inbound = segments[split_idx:]
    return outbound, inbound

def build_mtj_from_segments(
    segments: List[Dict[str, Any]],
    year: int,
    tpid: str,
    ptcs: str,
    gds: str,
    xpf: str,
    ndc_enabled: bool,
) -> str:
    """
    Build MTJ like:

    DA
    TPID 1
    PTCS ADT
    CTM O D MM/DD/YY
    [CTM ...]  (if RT)
    TMA 0 (...)
    [TMA 1 (...)]
    CI <TMA indices>
    [XMLREQUEST ...] (if ndc_enabled)
    GDS 1A
    XPF 30 0
    """
    if not segments:
        raise ValueError("No valid segments parsed from itinerary.")

    lines: List[str] = ["DA"]
    tpid = tpid.strip()
    ptcs = ptcs.strip().upper()
    gds = gds.strip().upper()
    xpf = xpf.strip()

    if tpid:
        lines.append(f"TPID {tpid}")
    if ptcs:
        lines.append(f"PTCS {ptcs}")

    ctms: List[str] = []
    tmas: List[str] = []

    def fmt_date(d: date) -> str:
        # Two-digit year, e.g. 09/15/26
        return d.strftime("%m/%d/%y")

    def build_bound(bound_segs: List[Dict[str, Any]], bound_index: int) -> None:
        if not bound_segs:
            return
        first = bound_segs[0]
        last = bound_segs[-1]

        # CTM: origin of first, dest of last, using first dep date
        ctms.append(f"CTM {first['orig']} {last['dest']} {fmt_date(first['dep_date'])}")

        # TMA n (...)
        seg_chunks = []
        for seg in bound_segs:
            seg_chunks.append(
                "("
                + " ".join(
                    [
                        seg["carrier"],
                        seg["flight"],
                        seg["orig"],
                        seg["dest"],
                        fmt_date(seg["dep_date"]),
                        seg["dep_time"],
                        fmt_date(seg["arr_date"]),
                        seg["arr_time"],
                        seg["booking_class"],  # add booking class at end
                    ]
                )
                + ")"
            )
        tmas.append(" ".join(["TMA", str(bound_index)] + seg_chunks))

    # Decide OW vs RT by dep_date
    outbound, inbound = split_bounds_by_date(segments)
    if outbound and not inbound:
        # One-way
        build_bound(outbound, bound_index=0)
    else:
        # Roundtrip (or multi-date) – first date group outbound, rest inbound
        build_bound(outbound, bound_index=0)
        build_bound(inbound, bound_index=1)

    # CTMs first, then TMAs
    lines.extend(ctms)
    lines.extend(tmas)

    # CI indices: CTMs are 0..len(ctms)-1, TMAs start at len(ctms)
    tma_indices = [len(ctms) + i for i in range(len(tmas))]
    if tma_indices:
        ci_line = "CI " + " ".join(str(i) for i in tma_indices)
    else:
        ci_line = "CI 0"

    lines.append(ci_line)

    # Optional NDC XML between CI and GDS
    if ndc_enabled:
        lines.append(NDC_XML)

    lines.append(f"GDS {gds}")
    lines.append(f"XPF {xpf} 0")

    return "\n".join(lines)

def main():
    st.title("GDS Itinerary → MTJ Converter")

    # Banner / instructions
    st.warning(
        "Note: Please do NOT add SSR / RM / OSI text in the input.\n\n"
        "Paste ONLY the flight segment lines in this format:\n\n"
        "1  AF8560 Y 15SEP 2 SANMSP HK1  1250 2  1330 1914   321 E 0 F\n\n"
        "2  AF 089 Y 15SEP 2 MSPCDG HK1  1920 1  2000 1115+1 772 E 0 BM\n\n"
    )

    # Default example in the clean format you want
    default_itin = (
        "1  AF8560 Y 15SEP 2 SANMSP HK1  1250 2  1330 1914   321 E 0 F\n"
        "2  AF 089 Y 15SEP 2 MSPCDG HK1  1920 1  2000 1115+1 772 E 0 BM\n"
    )

    with st.form("mtj_form"):
        itinerary_raw = st.text_area("Paste itinerary", value=default_itin, height=260)

        col1, col2 = st.columns(2)
        with col1:
            year = st.number_input("Travel year (YYYY)", 2000, 2100, 2026)
        with col2:
            tpid = st.text_input("TPID", value="1")
            ptcs = st.text_input("PTCS (e.g., ADT)", value="ADT")
            gds = st.selectbox("GDS", ["1A", "1S"], index=0)
            xpf = st.selectbox(
                "XPF (fare type mask)",
                ["30", "1", "2", "4", "8", "16"],
                index=0,
                help="30=all fares,1=pub,2=package,4=net,8=WL,16=web",
            )

        ndc_enabled = st.checkbox("NDC (add XMLREQUEST block)", value=False)

        submitted = st.form_submit_button("Generate MTJ")

    if not submitted:
        return

    try:
        segments = parse_segments(itinerary_raw, year=int(year))
        if not segments:
            st.error("Could not parse any flight segments from the input.")
            return

        mtj = build_mtj_from_segments(
            segments=segments,
            year=int(year),
            tpid=tpid,
            ptcs=ptcs,
            gds=gds,
            xpf=xpf,
            ndc_enabled=ndc_enabled,
        )
    except Exception as e:
        st.error(f"Error: {e}")
        return

    st.subheader("Parsed Segments (debug)")
    for s in segments:
        st.text(
            f"{s['carrier']} {s['flight']} {s['orig']}-{s['dest']} "
            f"{s['dep_date']} {s['dep_time']} -> {s['arr_date']} {s['arr_time']} "
            f"{s['booking_class']}"
        )

    st.subheader("MTJ Output")
    st.code(mtj, language="text")

if __name__ == "__main__":
    main()

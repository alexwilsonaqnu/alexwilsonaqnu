"""Generate the synthetic 3-page service manual used by the verification gates.

Test fixture only — reportlab is not imported anywhere at runtime.

    python scripts/make_test_manual.py [out.pdf]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "data" / "WTW5057LW0_service_manual.pdf"

PAGE_1 = [
    "WHIRLPOOL TOP-LOAD WASHER — SERVICE MANUAL",
    "Models: WTW5057LW0, WTW5057LW1",
    "",
    "SECTION 3 — DIAGNOSTICS: FILLS BUT DOES NOT AGITATE",
    "",
    "If the washer fills normally but the basket does not agitate, the shifter",
    "assembly is the first component to check. The shifter actuator engages the",
    "splutch cam; a failed shifter leaves the drive in spin position and no",
    "agitation occurs.",
    "",
    "Fault code F7E1 indicates a basket speed sensing fault. F7E1 is reported when",
    "the control detects motor rotation without corresponding basket rotation, and",
    "is commonly caused by a worn drive belt or a failed shifter position switch.",
    "",
    "Step 1. Enter Service Diagnostic Mode and read the stored fault codes.",
    "Step 2. Inspect the drive belt for glazing, cracking or slack. A belt that",
    "        deflects more than 12 mm under thumb pressure must be replaced.",
    "Step 3. Verify shifter actuator travel. Part number W11035747.",
]

PAGE_2 = [
    "SECTION 4 — COMPONENT ACCESS AND ELECTRICAL SAFETY",
    "",
    "WARNING: Electrical Shock Hazard. Disconnect power before servicing.",
    "",
    "The drive motor control capacitor stores a residual charge after the appliance",
    "is unplugged. Wait at least three minutes for the capacitor to discharge before",
    "touching any control board terminal. Verify zero volts across the capacitor",
    "terminals with a meter before proceeding.",
    "",
    "Failure to discharge the capacitor can result in serious injury or death.",
    "",
    "Refer to the wiring schematic below for capacitor and control board terminal",
    "locations on the machine control unit.",
]

PAGE_3 = [
    "SECTION 5 — LID LOCK AND WATER INLET VALVE",
    "",
    "The lid lock assembly must report locked before the control will allow",
    "agitation or spin. A lid lock that fails to latch produces fault code F5E2.",
    "Test the lid lock striker alignment and measure continuity across the lid",
    "lock switch terminals. Lid lock part number W11307244.",
    "",
    "The water inlet valve controls hot and cold fill. If the washer overfills or",
    "will not fill, test each inlet valve solenoid coil for resistance between",
    "800 and 1300 ohms. Replace the inlet valve assembly if a coil is open.",
    "",
    "Screen filters in the water inlet valve ports should be inspected for debris",
    "at every service call involving slow fill.",
]


def _schematic_png(path: Path) -> Path:
    """A small raster 'wiring schematic' so ingestion has a real embedded image to crop."""
    from PIL import Image, ImageDraw

    width, height = 520, 300
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, width - 5, height - 5], outline="black", width=2)
    draw.rectangle([60, 60, 220, 160], outline="black", width=2)
    draw.text((72, 100), "MACHINE CONTROL", fill="black")
    draw.rectangle([320, 80, 440, 200], outline="black", width=2)
    draw.text((338, 130), "CAPACITOR", fill="black")
    draw.line([220, 110, 320, 110], fill="black", width=2)
    draw.line([220, 140, 320, 140], fill="black", width=2)
    draw.line([140, 160, 140, 250], fill="black", width=2)
    draw.line([140, 250, 380, 250], fill="black", width=2)
    draw.line([380, 250, 380, 200], fill="black", width=2)
    draw.text((150, 255), "FIG. 4-2  WIRING SCHEMATIC", fill="black")
    image.save(path)
    return path


def build(out_path: Path) -> Path:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    out_path.parent.mkdir(parents=True, exist_ok=True)
    schematic = _schematic_png(out_path.parent / "_schematic_tmp.png")

    width, height = LETTER
    pdf = canvas.Canvas(str(out_path), pagesize=LETTER)

    def draw_lines(lines: list[str], start_y: float) -> float:
        y = start_y
        for line in lines:
            pdf.setFont("Helvetica-Bold" if line.isupper() and line else "Helvetica", 11)
            pdf.drawString(60, y, line)
            y -= 16
        return y

    draw_lines(PAGE_1, height - 70)
    pdf.showPage()

    y = draw_lines(PAGE_2, height - 70)
    pdf.drawImage(str(schematic), 60, y - 250, width=380, height=220)
    pdf.showPage()

    draw_lines(PAGE_3, height - 70)
    pdf.showPage()
    pdf.save()

    schematic.unlink(missing_ok=True)
    return out_path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    print(build(target))

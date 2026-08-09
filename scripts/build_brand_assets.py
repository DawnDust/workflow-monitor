"""Build deterministic Workflow Monitor brand assets from one geometry system."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ASSETS = ROOT / "project_hooks/ui/windows/assets"
WEB_ASSETS = ROOT / "project_hooks/ui/web/assets"
DOCS_ASSETS = ROOT / "docs/assets"

NAVY = "#10283A"
CYAN = "#0B8F87"
VIOLET = "#6657C8"
AMBER = "#B67A1F"
PAPER = "#F4F8F8"
MUTED = "#627785"


def mark_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Workflow Monitor">
  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <path d="M82 72L120 110L158 112M120 110L82 168" stroke="{NAVY}" stroke-width="20"/>
    <path d="M82 72L120 110L158 112M120 110L82 168" stroke="#D9E8E8" stroke-width="7"/>
  </g>
  <rect x="53" y="43" width="58" height="58" rx="10" fill="{CYAN}" transform="rotate(45 82 72)"/>
  <rect x="129" y="83" width="58" height="58" rx="10" fill="{VIOLET}" transform="rotate(45 158 112)"/>
  <rect x="53" y="139" width="58" height="58" rx="10" fill="{AMBER}" transform="rotate(45 82 168)"/>
  <circle cx="120" cy="110" r="19" fill="{NAVY}"/>
  <circle cx="120" cy="110" r="12" fill="{PAPER}"/>
  <circle cx="120" cy="110" r="7" fill="{CYAN}"/>
</svg>
"""


def wordmark_svg() -> str:
    mark = mark_svg().split(">", 1)[1].rsplit("</svg>", 1)[0]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 256" role="img" aria-label="Workflow Monitor">
  <style>
    .word {{ fill: {NAVY}; font: 700 74px 'Segoe UI', Arial, sans-serif; letter-spacing: -2px; }}
    .sub {{ fill: {MUTED}; font: 600 22px 'Segoe UI', Arial, sans-serif; letter-spacing: 7px; }}
    @media (prefers-color-scheme: dark) {{ .word {{ fill: #F4F8F8; }} .sub {{ fill: #A7B8C2; }} }}
  </style>
  <g>{mark}</g>
  <text class="word" x="250" y="118">Workflow</text>
  <text class="word" x="250" y="190">Monitor</text>
  <text class="sub" x="573" y="190">LOCAL · AUDITABLE</text>
</svg>
"""


def _node(size: int, color: str) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=color)
    return tile.rotate(45, resample=Image.Resampling.BICUBIC, expand=True)


def draw_mark(size: int) -> Image.Image:
    scale = max(4, 1024 // size)
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def point(x: int, y: int) -> tuple[int, int]:
        return x * canvas_size // 256, y * canvas_size // 256

    route = [point(82, 72), point(120, 110), point(158, 112)]
    branch = [point(120, 110), point(82, 168)]
    draw.line(route, fill=NAVY, width=20 * canvas_size // 256, joint="curve")
    draw.line(branch, fill=NAVY, width=20 * canvas_size // 256, joint="curve")
    draw.line(route, fill="#D9E8E8", width=7 * canvas_size // 256, joint="curve")
    draw.line(branch, fill="#D9E8E8", width=7 * canvas_size // 256, joint="curve")

    node_size = 58 * canvas_size // 256
    for center, color in (((82, 72), CYAN), ((158, 112), VIOLET), ((82, 168), AMBER)):
        node = _node(node_size, color)
        x, y = point(*center)
        image.alpha_composite(node, (x - node.width // 2, y - node.height // 2))

    x, y = point(120, 110)
    for radius, color in ((19, NAVY), (12, PAPER), (7, CYAN)):
        r = radius * canvas_size // 256
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def social_preview() -> Image.Image:
    image = Image.new("RGB", (1280, 640), "#08131E")
    draw = ImageDraw.Draw(image)
    for x in range(0, 1280, 48):
        draw.line((x, 0, x, 640), fill="#10212F", width=1)
    for y in range(0, 640, 48):
        draw.line((0, y, 1280, y), fill="#10212F", width=1)
    image.paste(draw_mark(430), (70, 105), draw_mark(430))
    draw.text((500, 190), "Workflow", font=_font("segoeuib.ttf", 92), fill=PAPER)
    draw.text((500, 290), "Monitor", font=_font("segoeuib.ttf", 92), fill=PAPER)
    draw.text((506, 420), "LOCAL  ·  AUDITABLE  ·  BUILT FOR RESEARCH", font=_font("segoeui.ttf", 25), fill="#9FB2BD")
    draw.rounded_rectangle((504, 472, 775, 482), radius=5, fill=CYAN)
    draw.rounded_rectangle((788, 472, 968, 482), radius=5, fill=VIOLET)
    draw.rounded_rectangle((981, 472, 1118, 482), radius=5, fill=AMBER)
    return image


def main() -> None:
    for directory in (WINDOWS_ASSETS, WEB_ASSETS, DOCS_ASSETS):
        directory.mkdir(parents=True, exist_ok=True)

    svg = mark_svg()
    (WEB_ASSETS / "workflow-monitor-mark.svg").write_text(svg, encoding="utf-8")
    (DOCS_ASSETS / "workflow-monitor-mark.svg").write_text(svg, encoding="utf-8")
    (DOCS_ASSETS / "workflow-monitor-wordmark.svg").write_text(wordmark_svg(), encoding="utf-8")

    icon = draw_mark(512)
    icon.save(WINDOWS_ASSETS / "workflow_monitor_icon.png", optimize=True)
    icon.save(
        WINDOWS_ASSETS / "workflow_monitor_icon.ico",
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    draw_mark(64).save(DOCS_ASSETS / "workflow-monitor-favicon.png", optimize=True)
    social_preview().save(DOCS_ASSETS / "workflow-monitor-social-preview.png", optimize=True)


if __name__ == "__main__":
    main()

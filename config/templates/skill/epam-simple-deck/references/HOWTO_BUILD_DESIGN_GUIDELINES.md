# Creating/updating design guidelines for a specific presentation

1. Read user's request
2. Read outline
3. If it exists, read existing `presentation.design`
4. Based on all these inputs and considering what's available in simple-deck and through python-pptx, please define design guidelines to keep overall deck's design consistent
5. Create or update `presentation.design` with design guidelines 

# What to include in design guidelines
- Image style, image border style (if BORDERED style)
- Diagram style (for nodes and connectors)
- Select block styles to be used across presentation
    - Styles that fit the best to the presentation's outline and user's request
    - Depending on outline size and complexity, limit block styles to 4-6 to keep consistency
    - Ideally to have 3-4 "general purpose" styles, 1-2 style with icons, and a hero style
- If process components are to be used, decide if you need both chevron and block process. For block process (if it's needed) decide on two styles that can be used for it in this deck.
- If blocks with images are to be used, decide on image style and image border style specifically for such blocks

---

=== AVAILABLE COLORS ===
| Color (EPAMColor) | Hex | Contrast on white | Contrast on black |
|---|---:|---:|---:|
| Cyan | #00F6FF | 1.34 | 15.63 |
| LightPurple | #B896FF | 2.37 | 8.84 |
| LightBlue | #7BA8FF | 2.36 | 8.89 |
| MediumBlue | #428DFF | 3.24 | 6.49 |
| MintGreen | #00F4AB | 1.45 | 14.53 |
| YellowGreen | #D5E662 | 1.37 | 15.32 |
| Green | #55E66F | 1.62 | 12.93 |
| Mint | #00F4AB | 1.45 | 14.53 |
| LightCyan | #89F0F0 | 1.33 | 15.79 |
| Blue | #428EFF | 3.21 | 6.54 |
| DeepBlue | #0047FF | 6.28 | 3.35 |
| NavyBlue | #0078C2 | 4.70 | 4.47 |
| BluePurple | #9BA4F8 | 2.32 | 9.06 |
| Purple | #8453D2 | 5.06 | 4.15 |
| Pink | #FF4B9B | 3.12 | 6.73 |
| LightPink | #F6969C | 2.15 | 9.76 |
| Red | #FF1F47 | 3.79 | 5.54 |
| RedOrange | #FF4D40 | 3.29 | 6.39 |
| Orange | #FF7701 | 2.66 | 7.89 |
| Gray | #8A8A8A | 3.45 | 6.08 |
| LightGray | #DEDEDE | 1.35 | 15.61 |
| OffWhite | #FBFAFA | 1.04 | 20.16 |
| Light_Blue | #0047FF | 6.28 | 3.35 |
| Light_Cyan | #0078C2 | 4.70 | 4.47 |
| Light_Purple | #8453D2 | 5.06 | 4.15 |
| Light_DeepBlue | #0135BB | 9.43 | 2.23 |
| Light_Teal | #107E8D | 4.78 | 4.39 |
| Light_Green | #00A705 | 3.22 | 6.53 |
| Light_Forest | #03815B | 4.89 | 4.30 |
| Light_Aqua | #08A499 | 3.10 | 6.78 |
| Light_Sky | #2590EE | 3.33 | 6.30 |
| Light_Royal | #0F23DB | 9.20 | 2.28 |
| Light_Lavender | #A080DB | 3.18 | 6.61 |
| Light_Violet | #893FFF | 4.98 | 4.22 |
| Light_Periwinkle | #6B6EC8 | 4.48 | 4.69 |
| Light_Mauve | #A84370 | 5.67 | 3.70 |
| Light_Orange | #E06C00 | 3.33 | 6.31 |
| Light_Rust | #CB3E01 | 4.97 | 4.23 |
| Light_Coral | #FF4D40 | 3.29 | 6.39 |
| Light_Red | #E80202 | 4.73 | 4.44 |
| Light_Crimson | #C40303 | 6.25 | 3.36 |
| Light_Magenta | #E614EB | 3.71 | 5.66 |
| Light_Gray | #898989 | 3.50 | 6.00 |
| Light_DarkGray | #4D4D4D | 8.45 | 2.48 |
| Black | #000000 | 21.00 | 1.00 |
| White | #FFFFFF | 1.00 | 21.00 |
| DarkGray | #1A1A1A | 17.40 | 1.21 |
| DarkGray2 | #2A2A2A | 14.35 | 1.46 |

---

=== SIMPLE-DECK USAGE GUIDE ===

# simple-deck Usage Guide

Comprehensive reference for creating EPAM-branded PowerPoint presentations with simple-deck package.

## Table of Contents

- [Getting Started](#getting-started)
- [Presentation Styles](#presentation-styles)
- [Slide Layouts](#slide-layouts)
- [Components](#components)
- [Color System](#color-system)
- [Positioning](#positioning-and-sizing)
- [Best Practices](#best-practices)

---

## Getting Started

```python
from simple_deck import (
    EPAMPresentation, Theme, EPAMColor, Background,
    Text, Header, Block, BlockGrid, ConceptMap, DiagramCanvas, Image, Icon,
    BlockProcess, ChevronProcess, ChevronStep, ProcessDirection,
    BlockStyle, ImageStyle, NodeStyle, ConnectionLineStyle, ConnectionConnectorStyle,
    TextSize, BlockMarginPreset, GapSize, ChevronWidth,
    HorizontalAlignment, VerticalAlignment, IconSize, IconType, NumberingType,
)

prs = EPAMPresentation("output/demo.pptx", theme=Theme.DEFAULT)
# Add content...
prs.save()
```

### Core Principles

**Measurements**: All values in EMU (914400 EMU = 1 inch). `content_area` properties (`.left`, `.top`, `.width`, `.height`) are in EMU—use directly.

**Positioning**: Elements positioned **relative to content_area**, NOT slide. Start at `left=0, top=0` within content_area.

**Dimensions**: Text/Header/Block/BlockGrid/BlockProcess will be at least as high as it's required to display their content even if explicit "height" parameter is less.
The same with width and height for ConceptMap.
Image will always keep aspect ratio of the source image and thus might ignore either width or height if both are specified.

**Background Matching**: Specify `background=Background.LIGHT` or `Background.DARK` to match slide type for proper colors.

**Content Areas**: Slide methods return `(slide, content_area)` tuple for element placement.

```python
slide, area = prs.add_default_slide_dark(upsubtitle="SECTION", title="Title")
prs.add_block(area, Block(left=0, top=0, width=area.width, height=1828800,
                          title="Block", content="Text"), background=Background.DARK)
```

### Size Constants

**TextSize**:
Sizes are in points (pt)

| TextSize | Content | Title | Header component | Hero metric |
| EXTRA_LARGE | 22 | 28 | 48 | 60 |
| LARGE | 18 | 24 | 36 | 40 |
| MEDIUM | 14 | 18 | 28 | 28 |
| SMALL | 11 | 14 | 20 | 22 |
| EXTRA_SMALL | 9 | 12 | 14 | 16 |

**IconSize** (icon dimensions):
- `SMALL` = 0.5" × 0.5" (457200 EMU) - Default for gradient block styles
- `MEDIUM` = 0.7" × 0.7" (640080 EMU) - Default for TOP_ICON/LEFT_ICON styles
- `LARGE` = 1.0" × 1.0" (914400 EMU)

**GapSize** (component gaps):
- `SMALL` = 0.1" (91440 EMU) for BlockGrid; 0.15" (137160 EMU) for vertical chevron
- `MEDIUM` = 0.2" (182880 EMU) for BlockGrid; 0.3" (274320 EMU) for vertical chevron
- `LARGE` = 0.6" (548640 EMU) for BlockGrid; 0.5" (457200 EMU) for vertical chevron

**BlockMarginPreset**
- `EXTRA_TIGHT`
- `NARROW`
- `REGULAR` (default)
- `WIDE`

**ChevronWidth** (vertical chevron width):
- `NARROW` = 0.9" (822960 EMU)
- `REGULAR` = 1.3" (1188720 EMU) - Default
- `WIDE` = 2.0" (1828800 EMU)

**SpaceAfter** (header spacing):
- `NONE` = No extra space
- `SMALL` = Small gap
- `REGULAR` = Standard gap
- `LARGE` = Large gap

---

## Slide Layouts

### Method Reference

| Layout Type | Method Name | Returns |
|-------------|-------------|---------|
| Cover Light | `add_cover_slide_light(presentation_title, description, date)` | `(slide, None)` |
| Cover Dark | `add_cover_slide_dark(presentation_title, description, date, image_path=None)` | `(slide, None)` |
| Contents Light | `add_contents_slide(contents)` | `(slide, None)` |
| Contents Dark | `add_contents_slide_dark(contents)` | `(slide, None)` |
| Section Header Light | `add_section_header_slide(section_title, section_description, section_number)` | `(slide, None)` |
| Section Header Dark | `add_section_header_slide_dark(section_title, section_description, section_number)` | `(slide, None)` |
| Default Slide | `add_default_slide(title=None, upsubtitle=None, subtitle=None)` | `(slide, content_area)` |
| Default Slide Dark | `add_default_slide_dark(title, upsubtitle, subtitle=None)` | `(slide, content_area)` |
| Full Picture | `add_full_picture_slide(image_path)` | `(slide, None)` |
| Content + Image Right | `add_content_and_image_to_the_right_slide(title, section_title, content_width_ratio, image_path)` | `(slide, content_area)` |
| Content + Image Right Dark | `add_content_and_image_to_the_right_slide_dark(title, section_title, content_width_ratio, image_path)` | `(slide, content_area)` |
| Grey + White Split | `add_grey_and_white_split_slide(title, section_title, grey_width_ratio)` | `(slide, left_content_area, right_content_area)` |
| Black + White Split | `add_black_and_white_split_slide(title, section_title, black_width_ratio)` | `(slide, left_content_area, right_content_area)` |
| White + Grey Split | `add_white_and_grey_split_slide(title, section_title, white_width_ratio)` | `(slide, left_content_area, right_content_area)` |

### Catalog & Pre-designed

**Catalog** - `add_catalog_slide(CatalogSlide.*)` - Pre-designed corporate slides, **Returns**: (slide, None)
- See `references/SLIDES_FROM_CATALOG.md` for full list

**Case Studies** - `add_case_study(CaseStudy.*)` - Pre-designed client project slides, **Returns**: [(slide, None), ...]
- See `references/CASE_STUDIES.md` for full list

---

## Components

All components require `background=Background.LIGHT` or `Background.DARK` for proper text/color rendering.

### Common Position/Size Parameters

All visual components share these positioning parameters (values in EMU):

| Parameter | Type | Description |
|-----------|------|-------------|
| `left` | int | Left position (EMU) relative to content_area. Specify `left` OR `right` |
| `right` | int | Right position (EMU) from right edge. Specify `left` OR `right` |
| `top` | int | Top position (EMU) relative to content_area. Specify `top` OR `bottom` |
| `bottom` | int | Bottom position (EMU) from bottom edge. Specify `top` OR `bottom` |
| `width` | int | Width in EMU |
| `height` | int | Height in EMU (0 = auto-height for some components) |

**Note**: Must specify exactly one horizontal anchor (`left` OR `right`) and one vertical anchor (`top` OR `bottom`).

### Text

Basic text element with full formatting control.

```python
prs.add_text(area, Text(left=0, top=0, width=5486400, height=914400, text="Hello",
                        font_size=24, bold=False, horizontal_alignment=HorizontalAlignment.CENTER),
             background=Background.LIGHT)
```

**Gradient text** — apply an EPAM-branded horizontal gradient fill:
```python
# Light background: Light_Blue → lightened Light_Blue (40% lighter)
prs.add_text(area, Text(left=0, top=0, width=10881360, height=548640,
                        text="Gradient Heading", font_size=40, bold=True,
                        gradient=True),
             background=Background.LIGHT)

# Dark background: Cyan → lightened Cyan (50% lighter)
prs.add_text(area, Text(left=0, top=0, width=10881360, height=548640,
                        text="Gradient Heading", font_size=40, bold=True,
                        gradient=True),
             background=Background.DARK)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | str | required | Text content |
| `font_size` | int | 14 | Font size in points |
| `font_color` | EPAMColor\|None | None | Text color (None=theme default); ignored when `gradient=True` |
| `bold` | bool | False | Bold text |
| `gradient` | bool | False | When True, applies EPAM-branded horizontal gradient fill using the theme accent color → lightened variant; `font_color` is ignored |
| `font_name` | str | "Calibri" | Font family |
| `horizontal_alignment` | HorizontalAlignment | LEFT | LEFT/CENTER/RIGHT |
| `vertical_alignment` | VerticalAlignment | TOP | TOP/MIDDLE/BOTTOM |
| `margin_left/right/top/bottom` | int\|None | None | Override text frame margins (EMU). None=defaults (73152 EMU) |

### Header

Styled header with predefined size scales.

```python
prs.add_header(area, Header(left=0, top=0, width=10881360, height=548640, text="Title",
                            text_size=TextSize.LARGE, color=EPAMColor.Light_Blue),
               background=Background.LIGHT)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | str | required | Header text |
| `text_size` | TextSize | MEDIUM |  |
| `color` | EPAMColor\|None | None | Header color (None=theme accent) |
| `horizontal_alignment` | HorizontalAlignment | LEFT | LEFT/CENTER/RIGHT |
| `space_after` | SpaceAfter | REGULAR | NONE/SMALL/REGULAR/LARGE |
| `bold` | bool | True | Bold text |
| `font_name` | str | "Calibri" | Font family |
| `margin_left/right/top/bottom` | int\|None | None | Override text frame margins (EMU). None=defaults (73152 EMU) |

### Block

Versatile content blocks with multiple style modes.

```python
prs.add_block(area, Block(left=0, top=0, width=2743200, height=0,  # height=0 for auto
                          title="Title", content="Text",
                          style=BlockStyle.CALLOUT, color=EPAMColor.Light_Blue,
                          text_size=TextSize.MEDIUM, margin_preset=BlockMarginPreset.NARROW),
              background=Background.LIGHT)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters) - note: `height=0` for auto-height):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | str\|None | None | Block title |
| `content` | str\|None | None | Plain text content |
| `rich_content` | list[ContentParagraph]\|None | None | Formatted content (use content OR rich_content) |
| `style` | BlockStyle | COLORED_TITLE | Visual style (see below) |
| `color` | EPAMColor\|None | None | Accent color |
| `text_size` | TextSize | MEDIUM | Preset: EXTRA_LARGE/LARGE/MEDIUM/SMALL/EXTRA_SMALL |
| `title_font_size/content_font_size` | int\|None | None | Override text_size |
| `margin_preset` | BlockMarginPreset | REGULAR |  |
| `horizontal_alignment` | HorizontalAlignment | LEFT | LEFT/CENTER/RIGHT |
| `vertical_alignment` | VerticalAlignment | TOP | TOP/MIDDLE/BOTTOM |
| `font_color` | EPAMColor\|None | None | Override default text color |
| `title_font_color` | EPAMColor\|None | None | Override title color only |
| `border/border_color` | bool\|None, EPAMColor\|None | None | Border visibility/color |
| `shadow` | bool | False | Drop shadow effect (not applicable for CALLOUT and TWO_TONE_FILL) |
| `icon_category/icon_name` | str\|None | None | Icon (required for icon styles) |
| `icon_type` | IconType | GRADIENT | GRADIENT/SOLID |
| `icon_size` | IconSize\|None | None | SMALL(0.5")/MEDIUM(0.7")/LARGE(1.0") |
| `image_path` | str\|None | None | Image path (required for IMAGE_TO_THE_LEFT and IMAGE_ON_TOP) |
| `image_size` | BlockImageSize | MEDIUM | Preset size for square image dimension for IMAGE_TO_THE_LEFT and IMAGE_ON_TOP |
| `image_crop` | ImageCrop | SQUARE | SQUARE/CIRCLE/NO_CROP |
| `image_horizontal_alignment` | HorizontalAlignment | CENTER | LEFT/CENTER/RIGHT for IMAGE_ON_TOP |
| `image_vertical_alignment` | VerticalAlignment | MIDDLE | TOP/MIDDLE/BOTTOM for IMAGE_TO_THE_LEFT |
| `image_style` | ImageStyle | SIMPLE | SIMPLE/SHADOWED/BORDERED for IMAGE_TO_THE_LEFT and IMAGE_ON_TOP |
| `image_border_style` | ImageBorderStyle | SINGLE | SINGLE/DOUBLE when image_style=BORDERED |
| `title_bar_width_percent` | int | 70 | Title bar width % (50-90, for ICON_AND_TITLE_ON_GRADIENT_BG) |
| `metric_value` | str\|None | None | Hero stat text, max 7 chars (e.g. "42.3B"). Required for HERO_METRIC_* styles; `title` must be None |
| `metric_font_size` | int\|None | None | Auto-computed from TEXT_SIZE_HERO_METRICS_MAP; override for custom size |
| `margin_left/right/top/bottom` | int\|None | None | Override text frame margins (EMU). None=style defaults. Left-side media modes: margin_left controls icon/image position from block edge. Top media modes: margin_top controls icon/image position from block edge. Gradient modes: margins apply to content area only |

**BlockImageSize**
- EXTRA_SMALL (228600 EMU, ~0.25")
- SMALL (457200 EMU, ~0.5")
- MEDIUM (914400 EMU, ~1")
- LARGE (1371600 EMU, ~1.5")

**Block Styles** (Visual Design):

**SOLID_FILL**: Colored background with subtle gradient (darker at top → lighter at bottom, 10% lightness span) with auto-contrast text. 

**ROUNDED_SOLID_FILL**: Same as SOLID_FILL, but with rounded outer corners.

**CALLOUT**: Neutral gray background with colored left accent border. See also CALLOUT_WITH_ACCENTED_BACKGROUND.

**CALLOUT_WITH_ACCENTED_BACKGROUND**: Same layout as CALLOUT (left accent border + 1-cell table) but uses a very light tint of the accent color (HLS lightness=90%) as the background instead of the neutral block background. Text color auto-derived for contrast (dark on light bg) unless overridden via `font_color`. 

**COLORED_TITLE**: Transparent background with colored title text only. 

**CARD**: Card with colored border and without fill. By default title and content uses primary text color.

**ROUNDED_CARD**: Same as CARD, but with rounded outer corners.

**SIMPLE**: Ultra-minimal styling with transparent background. No borders, accents or colors.

**TWO_TONE_FILL**: Two-section block rendered as a 2-row table. Row 0 (title): compact height, slightly darker accent fill (L-5%). Row 1 (content): fixed 90% lightness fill (S×0.80). Each section uses auto-contrast text (white/black) based on its own fill. No border by default; shadow is not supported.

**ICON_AND_TITLE_ON_GRADIENT_BG**: Icon at top-left corner (no margin) + horizontal gradient title bar extending to the right. Gradient bar width configurable (50-90%, default 70%). Subtle border (20% opacity). Content area below. Default icon size: SMALL (0.5"). 

**ICON_ON_GRADIENT_AND_GRADIENT_TITLE**: Icon on gradient square (default 0.8"×0.8", scales 1.6× icon size) with rounded corners at top-left + gradient-styled title text (horizontal gradient, left-aligned). Icon always SOLID type (black on light, white on dark) regardless of icon_type param. Subtle border (15% opacity). Content below. Default icon size: SMALL (0.5").

**TOP_ICON_AND_TITLE**: Large icon centered at top with padding + centered title below with accent color + content text below (respects horizontal_alignment). No background fill, optional border. Clean, minimalist. Default icon size: MEDIUM (0.7"). 

**IMAGE_ON_TOP**: Image at the top of the block with title below and content underneath. No background fill, optional block border. 

**LEFT_ICON_AND_TITLE**: Large icon at left side, vertically centered + title/content to the right. Title uses accent color, content uses default. Text area auto-adjusted for icon space. No background fill, optional border. Default icon size: MEDIUM (0.7"). 

**IMAGE_TO_THE_LEFT**: Image at left side with title/content to the right. No background fill, optional block border. 

**HERO_METRIC_TO_THE_LEFT**: Large bold metric value on the left (column reserved for 7 chars at hero metric font size, horizontally and vertically centered) + content text to the right. `metric_value` is required and must be ≤7 characters; `title` must not be set. Metric renders in accent color, bold. No background fill, optional border. 

**HERO_METRIC_ON_TOP**: Large bold metric value centered at the top of the block + content text below. `metric_value` is required and must be ≤7 characters; `title` must not be set. Metric renders in accent color, bold, centered. No background fill, optional border. 

**Rich Content** - `ContentParagraph` + `TextRun` for mixed formatting:
```python
from simple_deck import NumberingType

# Mixed formatting with runs
Block(..., rich_content=[
    ContentParagraph(runs=[TextRun(text="Bold", bold=True), TextRun(text=" and "),
                          TextRun(text="colored", color=EPAMColor.Cyan)], is_bullet=True),
    ContentParagraph(text="Simple paragraph", space_after_pt=12),  # Shorthand
])

# Numbered lists (DECIMAL, ALPHA_LOWER, ALPHA_UPPER, ROMAN_LOWER, ROMAN_UPPER)
Block(..., rich_content=[
    ContentParagraph(text="First step", numbering=NumberingType.DECIMAL, numbering_start=1),
    ContentParagraph(text="Second step", numbering=NumberingType.DECIMAL, numbering_start=2),
])
```

**ContentParagraph Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `runs` | list[TextRun]\|None | None | List of formatted text runs (use runs OR text) |
| `text` | str\|None | None | Shorthand for single run with default formatting |
| `is_bullet` | bool | False | Render as bullet item (mutually exclusive with numbering) |
| `numbering` | NumberingType\|None | None | Numbering format: DECIMAL/ALPHA_LOWER/ALPHA_UPPER/ROMAN_LOWER/ROMAN_UPPER |
| `numbering_start` | int | 1 | Starting number for numbering (e.g., 2 for "2." or "b.") |
| `alignment` | HorizontalAlignment\|None | None | Paragraph alignment (None=inherit from block) |
| `space_after_pt` | float | 0 | Space after paragraph in points |

**TextRun Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | str | required | Text content |
| `bold` | bool | False | Bold text |
| `italic` | bool | False | Italic text |
| `font_size` | int\|None | None | Font size in points (None=inherit from block) |
| `color` | EPAMColor\|None | None | Text color (None=inherit from block) |

---

### BlockGrid

Auto-layout blocks in grid. Blocks use dummy position/size `(0,0,0,0)` - grid controls layout. 3-tier inheritance: style defaults < grid-level < block-level.

```python
prs.add_block_grid(area, BlockGrid(left=0, top=0, width=10881360, height=0,
    blocks=[Block(left=0, top=0, width=0, height=0, title="F1", content="..."),
            Block(left=0, top=0, width=0, height=0, title="F2", content="...",
                  style=BlockStyle.COLORED_TITLE)],  # Override grid default
    columns=3, gap=GapSize.MEDIUM, style=BlockStyle.TWO_TONE_FILL,
    auto_color_rotation=True), background=Background.LIGHT)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters) - note: `height=0` for auto-height):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `blocks` | list[Block] | required | List of Block objects (use dummy 0,0,0,0) |
| `columns` | int | 3 | Blocks per row |
| `gap` | GapSize | MEDIUM | Gap: SMALL(0.1")/MEDIUM(0.2")/LARGE(0.6") |
| `style` | BlockStyle | SOLID_FILL | Default block style |
| `auto_color_rotation` | bool | True | Auto-cycle colors |
| `color` | EPAMColor\|None | None | Default color when auto_color_rotation=False and no color set on a block level |
| `text_size` | TextSize | MEDIUM | Default text size |
| `margin_preset` | BlockMarginPreset | REGULAR | Default margins for blocks |
| `icon_size` | IconSize\|None | None | Default icon size |
| `horizontal_alignment` | HorizontalAlignment\|None | None | Default alignment |
| `vertical_alignment` | VerticalAlignment\|None | None | Default vertical alignment |
| `title_font_size/content_font_size` | int\|None | None | Override text_size |
| `border/border_color/shadow` | bool\|None, EPAMColor\|None, bool\|None | None | Default visual effects |

Properties with 3-tier inheritance: `icon_size`, `margin_preset`, `alignments`, `text_size`, `font_sizes`, `color`, `border`, `border_color`, `shadow`

### ConceptMap

Radial diagram with center + 3-6 topics automatically arranged.

```python
prs.add_concept_map(area, ConceptMap(left=0, top=0, width=10881360, height=4572000,
    center_text="Core", center_radius=914400, style=ConceptMapStyle.DEFAULT,
    topics=[ConceptMapTopic(title="Strategy", description="..."),
            ConceptMapTopic(title="Design", description="...")]),
    background=Background.LIGHT)
```

ConceptMap center will be calculated based on left, top, width, height. But if actual content (based on topics, their content, topic radius, topics width and height) is larger than specified width and height, ConceptMap will take more space while center will stay fixed.

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `center_text` | str | required | Center ring text. Usually 1-2 words. |
| `topics` | list[ConceptMapTopic] | required | 3-6 topics around center |
| `center_radius` | int | 1371600 | Center ring radius (EMU, 1371600=1.5") |
| `style` | ConceptMapStyle | DEFAULT | Topic style (see below) |
| `topic_colors` | list[EPAMColor]\|None | None | Custom colors per topic |
| `horizontal_alignment` | HorizontalAlignment\|None | None | Force alignment (None=auto) |
| `topic_width/topic_height` | int\|None | None | Custom topic size (None=auto) |
| `topic_radius` | int\|None | None | Distance center to topics (None=center_radius*1.45) |
| `text_size` | TextSize | SMALL | Font size scale |

**ConceptMapTopic**: `ConceptMapTopic(title="...", description="...")`

**Styles**: DEFAULT (clean colored title with decorative lines), SOLID_FILL (solid colored bg), ROUNDED_SOLID_FILL (solid colored bg with rounded corners), CALLOUT (gray bg + left border), CALLOUT_WITH_ACCENTED_BACKGROUND (light colored bg + left border), COLORED_TITLE (transparent + colored title), CARD (bordered title block), ROUNDED_CARD (bordered title block with rounded corners), TWO_TONE_FILL (two-section: darker title + lighter content), SIMPLE (minimal)

### Process Components

Components for visualizing processes and workflows.

#### BlockProcess

Process visualization with blocks connected by arrows. Supports both horizontal (left-to-right) and vertical (top-to-bottom) directions. Each block is a full `Block` component, so you can use any `BlockStyle` (PANEL, CALLOUT, COLORED_TITLE, etc.).

**Horizontal Process** (default):
```python
from simple_deck import BlockProcess, Block, BlockStyle, ProcessDirection, GapSize, Background

prs.add_block_process(
    area,
    BlockProcess(
        direction=ProcessDirection.HORIZONTAL,  # Optional, default
        left=0, top=0,
        width=area.width,
        height=1371600,  # Or 0 for auto-height
        blocks=[
            Block(left=0, top=0, width=0, height=0, title="Step 1",
                  content="First step", style=BlockStyle.SOLID_FILL, color=EPAMColor.Light_Blue),
            Block(left=0, top=0, width=0, height=0, title="Step 2",
                  content="Second step", style=BlockStyle.CALLOUT, color=EPAMColor.Light_Cyan),
            Block(left=0, top=0, width=0, height=0, title="Step 3",
                  content="Third step", style=BlockStyle.COLORED_TITLE, color=EPAMColor.Light_Purple),
        ],
        gap=GapSize.MEDIUM,
        arrow_color=EPAMColor.Light_Gray,  # Optional - defaults based on background
    ),
    background=Background.LIGHT
)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `direction` | ProcessDirection | HORIZONTAL | Flow direction: HORIZONTAL (left-to-right) or VERTICAL (top-to-bottom) |
| `blocks` | list[Block] | required | 2-10 Block objects (position/size ignored, auto-calculated) |
| `gap` | GapSize | MEDIUM | Gap between blocks |
| `arrow_color` | EPAMColor\|None | None | Arrow color (None=auto: Light_Gray/Gray) |

**Notes**:
- Block positions and sizes in the `blocks` list are ignored - BlockProcess calculates them automatically
- Horizontal: Blocks flow left-to-right with RIGHT_ARROW shapes between them
- Vertical: Blocks stack top-to-bottom with DOWN_ARROW shapes between them
- Auto-height (`height=0`): Calculates block height based on the tallest block's content. All blocks use the same height for visual consistency

#### ChevronProcess

Chevron process flow with horizontal or vertical direction. Horizontal chevrons render 2-8 steps with labels inside and content below. Vertical chevrons render 2-5 steps stacked on left with content blocks on right.

```python
from simple_deck import ChevronProcess, ChevronStep, ProcessDirection, TextSize, HorizontalAlignment

prs.add_chevron_process(
    area,
    ChevronProcess(
        left=0, top=0,
        width=area.width,
        height=0,  # Auto-height
        direction=ProcessDirection.HORIZONTAL,
        steps=[
            ChevronStep(
                label="Discover",  # Max 10 chars for horizontal
                content="Research and analysis"
            ),
            ChevronStep(
                label="Define",
                content="Requirements gathering"
            ),
            ChevronStep(
                label="Design",
                content="Create prototypes"
            ),
        ],
        text_size=TextSize.MEDIUM,
        auto_color_rotation=True,  # Auto-assign colors
    ),
    background=Background.LIGHT
)

```

**ChevronProcess Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `direction` | ProcessDirection | required | HORIZONTAL (2-7 steps) or VERTICAL (2-4 steps) |
| `steps` | list[ChevronStep] | required | Chevron steps (must match direction) |
| `text_size` | TextSize | auto* | Font size for labels and content (*HORIZONTAL auto-scales; VERTICAL defaults to MEDIUM) |
| `auto_color_rotation` | bool | True | Auto-assign colors to steps |
| `color` | EPAMColor\|None | None | Default color when auto_color_rotation=False and no color set on a step level |
| `content_horizontal_alignment` | HorizontalAlignment\|None | None* | Alignment for content (*auto: CENTER for HORIZONTAL, LEFT for VERTICAL) |
| `chevron_width` | ChevronWidth | REGULAR | Width of chevron area for VERTICAL (NARROW=0.9", REGULAR=1.3", WIDE=2", ignored for HORIZONTAL) |
| `gap` | GapSize | auto* | Spacing between chevrons. For HORIZONTAL: SMALL=0.05", MEDIUM=0.2", LARGE=0.5" (*auto-scales). For VERTICAL: SMALL=0.15", MEDIUM=0.3", LARGE=0.5" (defaults to MEDIUM) |

**ChevronStep Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `label` | str | required | Text inside chevron (max 10 chars for HORIZONTAL, 15 for VERTICAL) |
| `content` | str\|None | None | Plain text below/beside chevron |
| `rich_content` | list[ContentParagraph]\|None | None | Formatted text below/beside chevron (mutually exclusive with content). Run colors are normalized to the content area's readable text color. |
| `color` | EPAMColor\|None | None | Chevron color (None=auto if auto_color_rotation=True) |

### Image

Image element with styles and crop options.

```python
prs.add_image(area, Image(left=0, top=0, width=3657600, height=0, image_path="photo.jpg",
                          style=ImageStyle.SHADOWED, shadow_blur=137160),
              background=Background.LIGHT)
```

**Sizing behavior**: when both `width` and `height` are provided, the image always scales proportionally to fit inside that bounding rectangle and is centered within it. When `image_crop` is `SQUARE` or `CIRCLE`, the source image is center-cropped first and then rendered into the largest square that fits inside the bounding rectangle.

**ImageCrop**
- NO_CROP (fit within bounding rectangle)
- SQUARE (center-crop to square)
- CIRCLE (center-crop to circle)

**ImageBorderStyle**
- SINGLE (one border)
- DOUBLE (two borders with a gap equal to `border_width`)

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters) - note: `height=0` or `width=0` for scaling that dimension automatically):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image_path` | str | required | Path to image file |
| `image_crop` | ImageCrop | NO_CROP |  |
| `style` | ImageStyle | SIMPLE | SIMPLE(no effects)/SHADOWED(drop shadow)/BORDERED(border overlay) |
| `border_color` | EPAMColor\|None | None | Border color (BORDERED mode) |
| `border_style` | ImageBorderStyle | SINGLE | |
| `shadow_blur` | int | 137160 | Blur radius EMU (0.15", SHADOWED mode) |
| `shadow_offset` | int | 73152 | Shadow offset EMU (0.08", SHADOWED mode) |
| `border_line_width` | int | 18288 | Width of each border line in EMU (BORDERED mode) |
| `border_width` | int | 45720 | Gap between the outer and inner border in EMU when `border_style=DOUBLE` |

### Icon

Icon from EPAM library. Auto-selects variant based on background.

```python
prs.add_icon(area, Icon(left=0, top=0, width=914400, height=914400, icon_category="Business",
                        icon_name="Teamwork", icon_type=IconType.GRADIENT),
             background=Background.LIGHT)
```

**Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `icon_category` | str | required | Icon category |
| `icon_name` | str | required | Icon name |
| `icon_type` | IconType | SOLID | GRADIENT(two-tone gradient fill, works with light and dark bg)/SOLID(white on dark, black on light) |


### DiagramCanvas

Custom diagram builder with flexible node positioning and smart connections. Create system architecture diagrams, flowcharts, and network diagrams with precise control over layout.

**Canvas-level defaults**: Set `node_style` and `node_fill_color` at canvas level to apply to all nodes that don't override them. 

```python
from simple_deck import DiagramCanvas, NodeStyle, ConnectionLineStyle, ConnectionConnectorStyle

# Create canvas with default node styling
canvas = DiagramCanvas(
    left=0, top=0, width=10972800, height=3657600,
    node_style=NodeStyle.ROUNDED_RECTANGLE,  # Default shape for all nodes
    node_fill_color=EPAMColor.Cyan,          # Default fill for all nodes
    fill_color=EPAMColor.LightGray,          # Canvas background (optional)
)

# Nodes inherit canvas defaults unless overridden
node1 = canvas.add_node(
    left=914400, top=914400,
    width=1828800, height=914400,
    content="Web App",  # Uses canvas fill (Cyan) and style (rounded)
)

node2 = canvas.add_node(
    left=4572000, top=914400,
    width=1828800, height=914400,
    content="API Gateway",
    fill_color=EPAMColor.LightPurple,  # Override fill, keep rounded style
)

node3 = canvas.add_node(
    left=8229600, top=914400,
    width=1828800, height=914400,
    content="Database",
    style=NodeStyle.RECTANGLE,           # Override style, keep canvas fill
)

# Add connections between nodes
canvas.add_connection(node1, node2, connection_text="HTTPS", end_arrow=True)
canvas.add_connection(
    node2, node3,
    line_style=ConnectionLineStyle.DASHED,
    connection_text="Query",
    end_arrow=True
)

# Render to slide
prs.add_diagram_canvas(area, canvas, background=Background.LIGHT)
```

**DiagramCanvas Parameters** (see [Common Position/Size Parameters](#common-positionsize-parameters)):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `nodes` | list[DiagramNode] | [] | List of nodes (use add_node() helper) |
| `connections` | list[DiagramConnection] | [] | List of connections (use add_connection() helper) |
| `node_style` | NodeStyle | ROUNDED_RECTANGLE | Default shape for nodes: RECTANGLE, ROUNDED_RECTANGLE, DECISION, MULTIDOCUMENT, DOCUMENT, STORAGE, OCTAGON, CIRCLE |
| `node_fill_color` | EPAMColor\|None | None | Default fill for nodes (None=no fill) |
| `border` | bool | False | Show canvas border |
| `border_color` | EPAMColor\|None | None | Canvas border color |
| `border_width` | int | 9144 | Canvas border width (0.01") |
| `fill_color` | EPAMColor\|None | None | Canvas background (None=transparent) |
| `node_shadow` | bool | True | Apply drop shadow to nodes by default |

**DiagramNode Parameters** (via `canvas.add_node()` - inherits Text formatting):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `left/right/top/bottom` | int | required | Position relative to canvas |
| `width` | int | required | Node width (EMU) |
| `height` | int | required | Node height (EMU) |
| `content` | str\|None | None | Plain text content |
| `rich_content` | list[ContentParagraph]\|None | None | Formatted content |
| `style` | NodeStyle\|None | None | RECTANGLE, ROUNDED_RECTANGLE, DECISION, MULTIDOCUMENT, DOCUMENT, STORAGE, OCTAGON, CIRCLE (None=inherit from canvas) |
| `fill_color` | EPAMColor\|None | None | Node fill (None=inherit from canvas node_fill_color) |
| `border` | bool | True | Show node border |
| `border_color` | EPAMColor\|None | None | Node border color |
| `border_width` | int | 9144 | Border width (0.01") |
| `shadow` | bool\|None | None | Show drop shadow (None=inherit from canvas node_shadow) |
| `font_size` | int | 14 | Font size (points) |
| `font_color` | EPAMColor\|None | None | Text color (None=auto-detect based on fill) |
| `bold` | bool | False | Bold text |
| `horizontal_alignment` | HorizontalAlignment | CENTER | Text horizontal alignment |
| `vertical_alignment` | VerticalAlignment | MIDDLE | Text vertical alignment |

**Fill Color Inheritance**: Node fill color resolution follows this cascade:
1. Node's explicit `fill_color` (if set)
2. Canvas `node_fill_color` (if set)
3. Canvas `fill_color` (if set)
4. None (transparent node)

**Node Shape Styles**: The `style` parameter (both at node and canvas level) controls node appearance. Ideal for different diagram types:
- `RECTANGLE`: rectangle with sharp corners
- `ROUNDED_RECTANGLE`: default; rounded corners for friendly appearance
- `DECISION`: diamond flowchart shape
- `MULTIDOCUMENT`: stacked documents symbol
- `DOCUMENT`: single document symbol
- `STORAGE`: stored data cylinder
- `OCTAGON`: stop/processing octagon
- `CIRCLE`: oval/circle shape

**Auto Font Color**: When `font_color=None`, text color is automatically chosen for contrast.

**Shadow Control**: By default, all nodes display a drop shadow. Disable shadows per-node with `shadow=False` or for all nodes with `node_shadow=False` on the canvas.

**Connection Parameters** (via `canvas.add_connection(start_node, end_node, ...)`):
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start_node` | DiagramNode | required | Starting node (returned by add_node()) |
| `end_node` | DiagramNode | required | Ending node |
| `connector_style` | ConnectionConnectorStyle | STRAIGHT | STRAIGHT, ELBOW, or CURVED |
| `line_style` | ConnectionLineStyle | SOLID | SOLID, DOTTED, or DASHED |
| `connection_text` | str\|None | None | Label text at connection midpoint |
| `start_arrow` | bool | False | Arrow at connection start |
| `end_arrow` | bool | True | Arrow at connection end |
| `line_color` | EPAMColor\|None | None | Line color (None=theme default) |
| `line_width` | int | 9144 | Line width (0.01") |

**Connection Styles:**
- **STRAIGHT**: Direct straight line between connection points (default, minimal path)
- **ELBOW**: Right-angle path with one horizontal and one vertical segment (organized layout)
- **CURVED**: Smooth curved path for aesthetic appearance (best for complex diagrams)

**Smart Connection Points**: Connections automatically select optimal attachment points based on node positions:
- **Horizontal**: Uses left/right edges when nodes are side-by-side
- **Vertical**: Uses top/bottom edges when nodes are stacked

**Node Positioning**: All node coordinates (left/top/width/height) are relative to the canvas, not the slide. Connectors stay attached when nodes are dragged in PowerPoint.

## Color System

**Light backgrounds** (`Background.LIGHT`): Use `Light_*` colors (Light_Blue, Light_Cyan, Light_Purple, Light_Teal, Light_Green, Light_Lavender, Light_Orange, Light_Mauve, etc.) - muted/toned for contrast.

**Dark backgrounds** (`Background.DARK`): Use vibrant colors (Cyan, LightPurple, LightBlue, MintGreen, Green, Orange, Pink, Blue, etc.) - bright for visibility.

**Base colors**: Black, White, DarkGray, DarkGray2

**Auto color rotation** (BlockGrid): Light cycle: Light_Blue, Light_Lavender, Light_Cyan, Light_Teal, Light_Orange, Light_Green, Light_Mauve, Light_DeepBlue. Dark cycle: Cyan, LightPurple, LightBlue, MintGreen, Orange, Green, Pink, Blue.

---

## Positioning and Sizing

**Units**: All measurements in EMU (914400 EMU = 1 inch). Positions relative to content_area origin (0,0) at top-left.

**Flexible anchoring**: Use `left`|`right` + `top`|`bottom`. Must specify one horizontal AND one vertical anchor.
- `left+top`: Anchor top-left (default), grows right/down
- `right+top`: Anchor top-right
- `left+bottom`: Anchor bottom-left
- `right+bottom`: Anchor bottom-right

**Auto-height** (`height=0`): With `top`, grows down. With `bottom`, grows up (for footers).

```python
Block(right=0, top=0, width=1828800, height=457200, ...)  # Top-right corner
Text(left=0, bottom=0, width=11064240, height=274320, ...)  # Bottom footer
```

**Typical content area**: ~12.1" × 5.1" at (0.5", 1.6") on slide.

---

## Best Practices

1. **Match background**: Use `background=Background.LIGHT` for light slides, `Background.DARK` for dark
2. **Color palettes**: `Light_*` colors for light bg, vibrant colors (Cyan, LightPurple) for dark bg
3. **Spacing**: Use GapSize/BlockMarginPreset enums. 
6. **Images**: Set either `height=0` or `width=0` for auto aspect ratio

---

## Complete Example

```python
from simple_deck import *

prs = EPAMPresentation("output/demo.pptx", theme=Theme.DEFAULT)
prs.add_cover_slide_light("Title", "Description", format_spaced_text("JANUARY 2026"))
prs.add_contents_slide(["Intro", "Approach", "Features", "Summary"])
prs.add_section_header_slide("Introduction", "Overview", "01")

slide, area = prs.add_default_slide(title="Approach", subtitle="Four phases")
prs.add_concept_map(area, ConceptMap(left=0, top=0, width=11064240, height=4572000,
    center_text="Strategy", style=ConceptMapStyle.DEFAULT,
    topics=[ConceptMapTopic(title="Discover", description="Research"),
            ConceptMapTopic(title="Design", description="Architecture")]),
    background=Background.LIGHT)

slide, area = prs.add_default_slide_dark(title="Features", upsubtitle="KEY")
prs.add_block_grid(area, BlockGrid(left=0, top=0, width=11064240, height=0,
    blocks=[Block(left=0,top=0,width=0,height=0, title="Scalability", content="..."),
            Block(left=0,top=0,width=0,height=0, title="Security", content="...")],
    columns=2, style=BlockStyle.SOLID_FILL, auto_color_rotation=True),
    background=Background.DARK)

prs.save()
```

---

## Troubleshooting

- **Wrong text colors**: Match `background=` param to slide type (LIGHT/DARK)
- **Wrong positions**: Positions relative to content_area (not slide). Start at (0,0)
- **Text cut off**: Use `height=0` for auto-height or reduce `text_size`
- **Wrong colors**: Use `Light_*` for light bg, vibrant (Cyan, etc.) for dark bg
- **Distorted images**: Set `height=0` for auto aspect ratio

**API Reference**: See `src/simple_deck/epam/{presentation,models,colors,constants,theme,styles}.py`

---

=== LAYOUTS REFERENCE ===
| Layout | Variant | Title | Description | Upsubtitle | Subtitle | Section Title | Section Number | Image | Background | Content Area | Notes |
|--------|---------|-------|-------------|------------|----------|---------------|----------------|-------|------------|--------------|-------|
| **Cover Light** | | 40 | 50 | | | | | | LIGHT | | Date: 30 chars. Use `format_spaced_text()` |
| **Cover Dark** | No image | 40 | 50 | | | | | | DARK | | Date: 30 chars |
| **Cover Dark** | With image | 20 | 25 | | | | | 6 099 048×6 858 000 EMU | DARK | | Date: 25 chars |
| **Contents** | Light | | | | | | | | LIGHT | | Max 6 items, 50 chars each |
| **Contents** | Dark | | | | | | | | DARK | | Max 6 items, 50 chars each |
| **Section Header** | Light | | | | | 25 | 3 | | LIGHT | | Description: 100-150 chars |
| **Section Header** | Dark | | | | | 15 | 3 | | DARK | | Description: 100-150 chars |
| **Default Slide** | Blank | | | | | | | | LIGHT | 11 274 552×5 943 600 at (457 200, 457 200) EMU | No text fields |
| **Default Slide** | Title only | 80 | | | | | | | LIGHT | 11 064 240×4 663 440 at (457 200, 1 463 040) EMU | |
| **Default Slide** | Title + Upsubtitle | 80 | | 30 | | | | | LIGHT | 11 064 240×4 663 440 at (457 200, 1 463 040) EMU | |
| **Default Slide** | Title + Subtitle | 80 | | 30 | 100 | | | | LIGHT | 11 064 240×4 297 680 at (457 200, 1 828 800) EMU | |
| **Default Slide Dark** | No subtitle | 80 | | 30 | | | | | DARK | 11 064 240×4 663 440 at (457 200, 1 463 040) EMU | Title + upsubtitle required |
| **Default Slide Dark** | With subtitle | 80 | | 30 | 100 | | | | DARK | 11 064 240×4 297 680 at (457 200, 1 828 800) EMU | |
| **Full Picture** | | | | | | | | 12 188 952×6 858 000 EMU | | | Image fills entire slide |
| **Content + Image Right** | 1/6 | 20 | | | | | | 9 082 800×6 858 000 EMU | LIGHT | ~2 295 144 wide, ~4 993 200 tall EMU | Image on right |
| **Content + Image Right** | 1/3 | 20 | | | | | | 7 876 800×6 858 000 EMU | LIGHT | ~3 493 008 wide, ~4 993 200 tall EMU | Image on right |
| **Content + Image Right** | 1/2 | 35 | | | | | | 6 094 800×6 858 000 EMU | LIGHT | ~5 294 376 wide, ~4 993 200 tall EMU | Image on right |
| **Content + Image Right Dark** | 1/6 | 20 | | | | | | 9 082 800×6 858 000 EMU | DARK | ~2 295 144 wide, ~4 993 200 tall EMU | Image on right |
| **Content + Image Right Dark** | 1/3 | 20 | | | | | | 7 876 800×6 858 000 EMU | DARK | ~3 493 008 wide, ~4 993 200 tall EMU | Image on right |
| **Content + Image Right Dark** | 1/2 | 35 | | | | | | 6 094 800×6 858 000 EMU | DARK | ~5 294 376 wide, ~4 993 200 tall EMU | Image on right |
| **Grey + White Split** | 1/3 | 20 | | | | 20 | | | Left: LIGHT, Right: LIGHT | Left: ~3 520 440, Right: ~7 077 456 EMU | Left grey, right white |
| **Grey + White Split** | 2/3 | 40 | | | | 40 | | | Left: LIGHT, Right: LIGHT | Left: ~6 153 912, Right: ~4 197 096 EMU | Left grey, right white |
| **Black + White Split** | 1/3 | 20 | | | | 20 | | | Left: DARK, Right: LIGHT | Left: ~3 520 440, Right: ~7 077 456 EMU | Left black, right white |
| **Black + White Split** | 2/3 | 40 | | | | 40 | | | Left: DARK, Right: LIGHT | Left: ~6 153 912, Right: ~4 197 096 EMU | Left black, right white |
| **White + Grey Split** | 1/2 | 40 | | | | 25 | | | Left: LIGHT, Right: LIGHT | Left: ~5 285 232, Right: ~5 285 232 EMU | Left white, right grey |
| **White + Grey Split** | 2/3 | 40 | | | | 25 | | | Left: LIGHT, Right: LIGHT | Left: ~7 095 744, Right: ~3 465 576 EMU | Left white, right grey |
| **Catalog Slide** | | | | | | | | | Varies | | Pre-designed corporate slides |
| **Case Study** | | | | | | | | | Varies | | Pre-designed client projects |




---

# Updating Python script for a presentation part
1. Read outline
    Identify slides for your part
2. Read design guidelines
3. Create/update script `presentation_part<X>.py`
    - For each slide:
        - based on the slide content, choose appropriate layout from available
        - design slide within layout 
        - using capabilities of EPAMPresentation and (when needed) python-pptx, create slide content in envisioned design
        - if the exact text from outline doesn't fit slide-level field like title, upsubtitle, subtitle, section title, etc, fill free to modify the content for these fields        
        - if technical/design parameter from outline is incorrect (e.g., wrong color or incorrect/missing icon), please fix it on your own
    - Save the Python script for the part
4. Execute the script to generate the PPTX
    prs.save() will automatically output spatial validation report to the console (additionally, the report will be saved into `<presentation name>_part<X>.report`)
5. Review the report in the console and fix issues if any (unless it was intentional overlap or overflow outside of content area). 
6. Repeat steps 4-5 until no unintentional issues found

- Update only script for your part, do not touch `presentation.py` script


# Script template for a presentation part
In `presentation_part<X>.py` where <X> is number of the part
```python
from simple_deck import EPAMPresentation, Background
from simple_deck import (
    Block,
    BlockGrid,
    BlockStyle,
    BlockProcess,
    BlockMarginPreset,
    BlockImageSize,
    ProcessDirection,
    ChevronProcess,
    ChevronWidth,
    ChevronStep,
    DiagramCanvas,
    ConnectionLineStyle,
    ConnectionConnectorStyle,
    NodeStyle,
    ConceptMap,
    ConceptMapTopic,
    Text,
    Image,
    ImageStyle,
    ImageCrop,
    ImageBorderStyle,
    ContentParagraph,
    TextRun,
    HorizontalAlignment,
    VerticalAlignment,    
    TextSize,
    GapSize,
    EPAMColor,
)

def generate_part<X>(prs: EPAMPresentation):
    # where new slides and their content will be added
    pass

def test_part<X>():
    prs = EPAMPresentation(
        output_path="presentation_part<X>.pptx"
    )

    generate_part<X>(prs)

    # Save the presentation — validation report printed automatically
    prs.save()
    print("Presentation created: presentation_part<X>.pptx")


if __name__ == "__main__":
    test_part<X>()
``` 

## Example script for a presentation part

```python
from simple_deck import EPAMPresentation, Background
from simple_deck import (
    Block,
    BlockGrid,
    BlockStyle,
    Text,
    Image,
    ImageStyle,
    ContentParagraph,
    TextRun,
)
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR_TYPE
from pptx.util import Pt

def generate_part1(prs: EPAMPresentation):
    # Add dark cover slide
    prs.add_cover_slide_dark(
        presentation_title="Project Overview",
        description="Q1 2026 Status Update",
        date="J A N U A R Y  2 0 2 6",
    )

    # Add section header
    prs.add_section_header_slide_dark(
        section_title="Key Achievements",
        section_description="Summary of Q1 accomplishments",
        section_number="01",
    )

    # Add status overview slide with content and image
    slide_status, content_status = prs.add_content_and_image_to_the_right_slide(
        title="Status overview",
        section_title="PROJECT STATUS",
        content_width_ratio="1/2",
        image_path="business-success.png",
    )

    # Add hero metric block
    prs.add_block(
        content_status,
        Block(
            left=content_status.width / 6,
            top=0,
            width=content_status.width / 3,
            height=0,
            metric_value="GREEN",
            content="Good progress",
            style=BlockStyle.HERO_METRIC_ON_TOP,
        ),
        background=Background.LIGHT,
    )

    prs.add_block(
        content_status,
        Block(
            left=4 * content_status.width / 6,
            top=0,  
            width=content_status.width / 3,
            height=0,
            metric_value="98%",
            content="completed scope",
            style=BlockStyle.HERO_METRIC_ON_TOP,
        ),
        background=Background.LIGHT,
    )

    # Add BlockGrid with highlights and risks
    prs.add_block_grid(
        content_status,
        BlockGrid(
            left=0,
            top=1828800,  # Below the two metric blocks
            width=content_status.width,
            height=0,
            columns=1,
            blocks=[
                Block(
                    left=0, top=0, width=0, height=0,
                    title="Highlights",
                    rich_content=[
                        ContentParagraph(
                            runs=[TextRun(text="Agreed on the deployment approach")],
                            is_bullet=True,
                        ),
                        ContentParagraph(
                            runs=[TextRun(text="Provisioned infrastructure")],
                            is_bullet=True,
                        ),
                    ],
                ),
                Block(
                    left=0, top=0, width=0, height=0,
                    title="Risks",
                    rich_content=[
                        ContentParagraph(
                            runs=[TextRun(text="Vendor X hasn't provided commitment on performing his part of activities on time")],
                            is_bullet=True,
                        ),
                        ContentParagraph(
                            runs=[TextRun(text="Vacation season is approaching")],
                            is_bullet=True,
                        ),
                    ],
                ),
            ],
            style=BlockStyle.CALLOUT,
        ),
        background=Background.LIGHT,
    )

    slide2, content2 = prs.add_default_slide(
        title="Architecture Overview",
        upsubtitle="TECHNICAL DESIGN",
    )

    prs.add_image(
        content2,
        Image(
            left=0,
            top=0,
            width=5486400,      # ~6" wide
            height=0,           # auto-height — aspect ratio preserved
            image_path="architecture-diagram.png",
            style=ImageStyle.SHADOWED,
        ),
        background=Background.LIGHT,
    )

    slide3, left_area, right_area = prs.add_white_and_grey_split_slide(
        title="Sprint Progress",
        section_title="BURN-DOWN",
        white_width_ratio="1/2",
    )

    _chart_left   = 457200
    _chart_top    = 1181100
    _chart_width  = 5278799   
    _chart_height = 4992900
    chart_data = ChartData()
    chart_data.categories = [
        "Sprint 1", "Sprint 2", "Sprint 3", "Sprint 4",
        "Sprint 5", "Sprint 6", "Sprint 7", "Sprint 8",
    ]
    chart_data.add_series("Ideal",  (80, 70, 60, 50, 40, 30, 20, 10))
    chart_data.add_series("Actual", (80, 74, 62, 51, 47, 35, 28, None))  # None = future sprint
    slide3.shapes.add_chart(
        XL_CHART_TYPE.LINE,
        _chart_left,
        _chart_top,
        _chart_width,
        _chart_height,
        chart_data,
    )

    prs.add_block(
        right_area,
        Block(
            left=0,
            top=914400,          
            width=right_area.width,
            height=0,
            title="Overall Status",
            content=(
                "7 of 8 sprints completed. "
                "Remaining backlog: 28 story points across 12 tickets."
            ),
            style=BlockStyle.COLORED_TITLE,
        ),
        background=Background.LIGHT,
    )
    prs.add_block(
        right_area,
        Block(
            left=0,
            top=2286000,
            width=right_area.width,
            height=0,
            title="Velocity",
            content=(
                "Average team velocity: 52 pts/sprint. "
                "Last sprint: 55 pts — above target."
            ),
            style=BlockStyle.COLORED_TITLE,
        ),
        background=Background.LIGHT,
    )


def test_part1():
    prs = EPAMPresentation(
        output_path="presentation_part1.pptx"
    )

    generate_part1(prs)

    # Save the presentation — validation report printed automatically
    prs.save()
    print("Presentation created: presentation_part1.pptx")

if __name__ == "__main__":
    test_part1()

```

# Important
- Use `background=Background.DARK` for components on dark slides
- Use `background=Background.LIGHT` for components on light slides
- Position components relative to the content area (not the slide)
- left, top, right, bottom, width, height are keyworded parameters, do not pass them as positional parameters
- `auto_color_rotation=True` automatically assigns different colors to blocks in a grid
- When using layout with image placeholder, ensure that image aspect ratio fits aspect ratio of the placeholder. If it doesn't use another layout without image placeholder and add image(s) manually.
- For a list inside a block use rich text formatting, do not use emulation with plain text symbols like "•" or "1. "
- when components from EPAMPresentation don't allow to include into a slide what's needed, it's ok to work directly with python-pptx capabilities. But slide itself should always be added via EPAMPresentation.

## Design tips & tricks
* Ensure slides fit into nice story arc, and logically follow each other
* Using blocks with icons can make a slide more visually interesting (but use your judgement when it makes sense)
* Having some variety in layouts might add some dynamic into the deck (but don't change layout just for the sake of changing - always select the most appropriate layout for the slide).
* Keep some level of consistency - you want to use similar design for the closely related content. E.g., if information about steps of a single process is split between several slides, you would want to keep these slides broadly similar.
* Balance variety and consistency - there should be some variety in design (layouts, styles, composition, etc), yet the presentation should overall look consistent.
* When selecting layout, consider amount of information to be included into a slide - e.g., for a slide with 4+ content-heavy blocks you should not select content and image layout, especially with narrow content area 
* If block's content contains enumeration of 3+ elements, and the enumeration is a key content of this block, strongly consider presenting that as a rich format list inside a block.
    E.g., 'Pilot products, enterprise skills repo, architecture gates, observability' should be transformed into rich format list with 4 items: "Pilot products", "Enterprise skills repo", "Architecture gates", "Observability". Or 'Developer co-pilot for local drafting, test generation, documentation, and refactoring.' can be transformed into 'Developer co-pilot for:' followed by rich format list consisting of 'local drafting', 'test generation', 'documentation' and 'refactoring' elements.
    When transforming into a list, for each list element remove plain text symbols denoting list elements like "•".
* Consider using block(s) with one of hero metric styles for prominent numbers
* Block style TWO_TONE_FILL doesn't look well when there are several such blocks each containing single short sentence with plain formatting.
* If on a page there are only 1-3 blocks in one row, and each block has just a few sentences or a list with up to 5 elements, or there is just one block, consider having them under each other in one column (e.g., via BlockGrid with columns = 1), and adding relevant image to the right (either using specific layout with image, or as Image component - depending on a case). Or adding relevant diagram. 
* Consider if you can illustrate some points with charts or diagrams, or visualize the main point in some other way.
* Don't use the same image over and over again.
* If there is just one row of blocks with relatively compact content (e.g., 1-2 paragraphs, or up to 7 list items) do NOT make them take entire content area's height, use auto-scaling instead
* Use ChevronProcess or BlockProcess to present not only processes, but sequential steps as well
* Use DiagramCanvas for cases like complex process with branches or cycles, architecture diagram or complex relationship between entities
* When selecting main colors for text/titles, consider expected background and contrast - text should have good contrast ratio to allow easy reading.
* If you need to provide very short info about a person like name + surname + position together with a photo, depending on number of such people use either:
    - block with style = IMAGE_ON_TOP
    - when you have more than 2 rows in a grid (e.g., 7+ people on one slide), use block with style = IMAGE_TO_THE_LEFT, vertical_alignment=VerticalAlignment.MIDDLE 
  In either case use image_crop = ImageCrop.CIRCLE, no title, and have first paragraph in rich_content be person's name + surname in bold and with 14 font size, while its position should be next paragraph with font size 12 and in italic. Keep border color default.
  If there is more information about a person than just a position, considering using title and content/rich_content instead of packing everything into rich_content.
* If you need to provide intro slide with short info about several people, you may use BlockGrid with large gaps



### Making layout tighter to conserve space
- Consider these techniques **ONLY** if you can't fit required content with default gap/text size/margin preset/etc or it's a part of explicit design decision as with default gap/text size/margin preset standard components look a little bit better.
- If can't fit content with default settings, don't apply all the techniques below at once, try with some reasonable subset first.

- To make Block take less space, you may play with:
    - style 
    - margin preset (e.g., change to BlockMarginPreset.NARROW)
    - text_size (e.g., set to TextSize.SMALL or even TextSize.EXTRA_SMALL)
    - removing title
- If your BlockGrid overflows content area, and you want to keep content of the grid, you can consider some of the following options (in the order of preference):
    - set text_size to TextSize.SMALL
    - consider different style for blocks (e.g., you might check if switching to icon/hero metric to the left instead of top can make block take less space for your given configuration)
    - set gap to GapSize.SMALL
    - set margin_preset to BlockMarginPreset.NARROW
    - set text_size to TextSize.EXTRA_SMALL    
- if there is too much text for the available space and no way to fit BlockGrid/separate blocks for each item, consider using one block with rich content instead of BlockGrid
- To have ConceptMap take less space, you may consider:
    - changing topics content
    - reducing topic radius - but you shouldn't set it less than 140% of center radius
    - set text_size to TextSize.EXTRA_SMALL
  If to specifically decrease height you might increase topics width (to decrease number of lines for each topic's block)

## Available colors and their contrast ratios
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

## Outline file format
- Slides are separated with "====="
- "# PART <X>: <part name>" is used for denoting parts boundaries

## Fixing issues from validation report
- Pay attention to actual sizes that are shown in the report.
- Actual height for a Block/Text/Header might be more than explicitly - depending on the content. Decreasing height further will not change ACTUAL component's height.
- Actual dimensions for a ConceptMap might be more than specified - depending on the topics content, topic radius and topic width/height. Decreasing dimensions further will be not change ACTUAL component's dimensions. 


## Additional information
- Information about available case studies is in [Case studies](references/CASE_STUDIES.md)

## Additional tools
### Getting information about image
Use scripts/image_dimensions.py to get that - path to the image should be provided as a command-line argument.

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

=== AVAILABLE ICONS ===
Icons are split into categories. 
Full list of available icons is in references/ICONS.md. The format is 
```
## Available Icons by Category

### <category name>

<icon_name>
<icon_name>
...

### <category name>

<icon_name>
<icon_name>
...

...

```
Consider using grep to get the full list of icons from the categories you need.

## Icon categories

| Category | Description | Icon count |
|--------|-------------|-------|
| **Advanced_Technology** | 5G, agility, cloud computing, CMS, and hierarchy concepts | 10 |
| **AI_VR_Bot** | Artificial intelligence, virtual reality, robots, 3D cubes, and brain/neural concepts | 39 |
| **Arrows** | Directional arrows, circular arrows, and navigation indicators | 13 |
| **Buildings_Construction** | Blueprints, bricks, construction, and architectural elements | 22 |
| **Business** | Business transformation, charts, contracts, teamwork, delivery, and corporate concepts | 14 |
| **Calendars_Timing** | Calendars, scheduling, alerts, time management, and date-related concepts | 13 |
| **Checkmarks** | Checkmarks in various styles (boxes, circles, shields) for validation and completion | 7 |
| **Cloud** | Cloud computing, cloud storage, sync, and cloud-based services | 7 |
| **Community** | Community engagement, code of conduct, volunteering, and social responsibility | 14 |
| **Core_Engineering** | Coding, design, development, and core engineering practices | 10 |
| **Data_Analytics_Charts** | Bar charts, graphs, pie charts, and data visualization elements | 20 |
| **Data_Analysis** | Data analysis, brain power, calculations, and analytical concepts | 18 |
| **Database** | Database operations, storage, alerts, connections, and data management | 14 |
| **Design_Marketing** | A/B testing, design tools, marketing, and creative concepts | 13 |
| **Devices_Media** | Cameras, computers, devices, screens, and media equipment | 34 |
| **Digital_Engagement** | Digital banking, applied insights, online engagement, and digital transformation | 10 |
| **Documents** | Books, bookmarks, files, folders, and document management | 20 |
| **Education** | Books, learning, graduation, and educational concepts | 40 |
| **Engineer** | Cross-device development, data funnels, data transfer, and engineering workflows | 18 |
| **Environment** | Sustainability, carbon footprint, green energy, and environmental concepts | 16 |
| **EPAM_Values_and_Personalities** | Awards, trophies, values, recognition, and company culture concepts | 10 |
| **Everyday** | Backpacks, books, glasses, and everyday objects | 11 |
| **Finance** | Banking, ATMs, cryptocurrency, money, and financial services | 35 |
| **Global** | Global operations, worldwide connectivity, and international concepts | 9 |
| **Goals** | Awards, achievements, targets, trophies, and success concepts | 18 |
| **Help** | Donations, support, charity, and assistance concepts | 8 |
| **Industry_Solutions** | Industry-specific solutions including energy, payments, and documents | 12 |
| **Intelligent_Enterprise** | Business intelligence, enterprise systems, servers, and smart business concepts | 4 |
| **Life_Sciences_Healthcare** | Medical equipment, healthcare, pharmaceuticals, and life sciences | 40 |
| **Office** | Career development, workplace awards, and office-related concepts | 11 |
| **People** | Groups of people, teams, individuals, and human figures | 46 |
| **Processes** | Gears, workflows, automation, and process-related concepts | 35 |
| **Retail** | Shopping, e-commerce, customer support, and retail concepts | 11 |
| **Strategy** | Briefcases, buildings, planning, and strategic business concepts | 24 |
| **Structures_Architectures** | Diagrams, flowcharts, architecture patterns, and structural concepts | 14 |
| **Tasks** | Checklists, task management, to-do items, and project tracking | 8 |
| **Transport_Travel** | Airplanes, travel, transportation, and mobility concepts | 16 |
| **Web** | Web elements, audio, calendars, content filtering, and online concepts | 26 |
| **Workshop** | Clipboards, clocks, manufacturing notes, and workshop-related concepts | 14 |

---

=== CATALOG SLIDES ===

# Slides from Catalog

Pre-designed corporate information slides from the EPAM catalog. 

## Available Catalog Slides

### CatalogSlide.EPAM_QUICK_FACTS_LIGHT
Company overview slide with Q3 2025 financial statistics, revenue breakdown by industry vertical (pie chart), and geographic distribution (pie chart). Shows 62,350+ employees, $1.394B Q3 revenue, presence in 55+ countries. Light background.

### CatalogSlide.EPAM_QUICK_FACTS_DARK
Identical content to EPAM_QUICK_FACTS_LIGHT with dark background styling. Same statistics and pie charts presented with white/cyan text on dark background for visual consistency in dark-themed presentation sections.

### CatalogSlide.EPAM_GEOGRAPHY_LIGHT
World map showing EPAM's global footprint across 55+ countries. Displays regional breakdowns for North America, CEE, Western Europe, Western & Central Asia, Middle East, LATAM, and APAC with complete country listings. Blue dots mark office locations. Light background.

### CatalogSlide.EPAM_GEOGRAPHY_DARK
Identical world map and geographic information as EPAM_GEOGRAPHY_LIGHT with dark background styling for consistency with dark-themed sections.

### CatalogSlide.EPAM_CONTINUUM
EPAM Continuum brand positioning slide with tagline "The Future. Made Real.™" and description of integrated consulting approach. Split design: white left block with text, dark right block with artistic light trail photography. Mixed light/dark background works in any context.


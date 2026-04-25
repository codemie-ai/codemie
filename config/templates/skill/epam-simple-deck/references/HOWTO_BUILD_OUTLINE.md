# Creating outline
1. Read file with user's request
2. Thoroughly analyze your task
3. Analyze all the available information
4. Build logical and engaging story arc
5. Identify required slides so that transitions are smooth and supporting overall story
6. Provide exact content to be included for each slide 
    - Do not include too specific suggestions about design/visual treatment or exact layout name, it will be done at later stage. But adding high-level suggestion about the layout is fine.
    - Follow character size limitations on slide title/upsubtitle/subtitle, section title, etc for the expected layout (see layouts quick reference below)
    - if the slide(s) should be a slide from the catalog or a case study, give exact identifier for the case study or catalog slide
    - if image should be added, describe in details what the image should contain
    - if an icon should be included, please specify exact category and icon name for each icon
7. Split entire outline into parts, target ~4-6 slides per part depending on part's complexity
8. Save outline into `presentation.outline`

# Updating outline
- Read existing outline from `presentation.outline`
- Thoroughly analyze request
- Consider what should be changed
- Make the required changes
- Save updated `presentation.outline`

# Format
- Include cover slide
- Consider "section divider" slides
- Separate slides with "====="
- Use "# PART <X>: <part name>" format for denoting parts

# Layouts quick reference
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


---

=== CASE STUDIES ===

# Catalog of Case Studies

Pre-designed corporate case study slides from the EPAM catalog. These professionally designed slides showcase EPAM's real-world client engagements and technical capabilities across various industries and domains.

## Available Case Studies

### Enterprise_Claims_System_Modernization_on_AWS
**Industry**: Insurance (Personal Lines & Commercial Lines)
**Focus**: Legacy Modernization, Cloud Migration, AWS
**Slides**: 1

Modernization of a decades-old mainframe-based claims system for a large personal lines and commercial lines insurer. The engagement involved comprehensive assessment, POC execution, architecture design, and data migration strategy to replace IMS/DB2 legacy systems with modern AWS cloud-based claims processing for personal insurance lines. Key achievements include target state definition, claims capability alignment, and business continuity planning.

**Key Technologies**: AWS, Mainframe Migration, IMS/DB2, Data Migration
**Use Case**: Demonstrate legacy system modernization and cloud transformation expertise

---

### Agentic_Data_Modernization
**Industry**: Healthcare
**Focus**: AI/ML, Autonomous Agents, Data Platform Modernization
**Slides**: 1

Implementation of an AI-driven agentic platform architecture for a healthcare provider. The solution showcases autonomous agent systems for intelligent data modernization, featuring a comprehensive platform architecture with multiple specialized agents working collaboratively for data transformation and management.

**Key Technologies**: Agentic AI, Autonomous Systems, Modern Data Platforms
**Use Case**: Showcase cutting-edge AI/ML capabilities and intelligent automation

---

### Transforming_Healthcare_Claims_Analytics_with_Data_Pipeline_Modernization
**Industry**: Digital Health
**Focus**: Data Engineering, Analytics, Healthcare Claims
**Slides**: 1

Data warehouse migration from PostgreSQL to Databricks for a digital health company delivering personalized therapy and behavioral health support. Implemented Kafka-based universal data ingestion framework with streaming capabilities for real-time and batch processing of complex healthcare claims data. The solution enables validation of clinical interventions and optimization of care pathways through comprehensive analytics.

**Key Technologies**: Databricks, Apache Kafka, PostgreSQL, Streaming Data, Healthcare Claims
**Key Results**: Improved data processing capabilities, enhanced clinical outcome measurement, cost-efficient resource utilization
**Use Case**: Demonstrate data engineering excellence and healthcare industry expertise

---

### Large_Wealth_Management_Company_Micro_Frontends_Development
**Industry**: Financial Services (Wealth Management)
**Focus**: Frontend Architecture, Team Scalability, DevOps
**Slides**: 1

Implementation of micro-frontend (MFE) architecture across 20+ teams for a large wealth management company. Addressed deployment bottlenecks, scalability constraints, and technology lock-in challenges inherent in monolithic frontend architectures. The solution enabled independent team velocity while maintaining enterprise-grade reliability and security standards.

**Key Results**:
- Time-to-market reduced by 75% (from 12 days to 3 days average)
- 20 deployments per team per sprint
- Eliminated constant cross-team coordination overhead
- Enabled independent technology adoption without full rewrites
- MFEs easily integrated into AI solutions (agents, chatbots) via MCP

**Key Technologies**: Micro-Frontends, Enterprise JavaScript, DevOps, CI/CD
**Use Case**: Demonstrate frontend architecture expertise and enterprise scalability solutions

---

## Usage

### Basic Usage

```python
from simple_deck import EPAMPresentation, CaseStudy

prs = EPAMPresentation("output/case_studies.pptx")

# Add individual case studies
prs.add_case_study(CaseStudy.Enterprise_Claims_System_Modernization_on_AWS)
prs.add_case_study(CaseStudy.Agentic_Data_Modernization)
prs.add_case_study(CaseStudy.Transforming_Healthcare_Claims_Analytics_with_Data_Pipeline_Modernization)
prs.add_case_study(CaseStudy.Large_Wealth_Management_Company_Micro_Frontends_Development)

prs.save()
```

### Industry-Specific Presentations

```python
from simple_deck import EPAMPresentation, CaseStudy, format_spaced_text

prs = EPAMPresentation("output/healthcare_capabilities.pptx")

# Cover
prs.add_cover_slide_light(
    presentation_title="Healthcare Solutions",
    description="EPAM Digital Health Capabilities",
    date=format_spaced_text("FEBRUARY 2026"),
)

# Healthcare-focused case studies
prs.add_case_study(CaseStudy.Agentic_Data_Modernization)
prs.add_case_study(CaseStudy.Transforming_Healthcare_Claims_Analytics_with_Data_Pipeline_Modernization)

prs.save()
```

### Financial Services Proposal

```python
from simple_deck import EPAMPresentation, CaseStudy, CatalogSlide, format_spaced_text

prs = EPAMPresentation("output/finserv_proposal.pptx")

# Cover
prs.add_cover_slide_light(
    presentation_title="Financial Services Modernization",
    description="Digital Transformation Solutions",
    date=format_spaced_text("FEBRUARY 2026"),
)

# Company overview
prs.add_catalog_slide(CatalogSlide.EPAM_QUICK_FACTS_LIGHT)

# Financial services case studies
prs.add_case_study(CaseStudy.Enterprise_Claims_System_Modernization_on_AWS)
prs.add_case_study(CaseStudy.Large_Wealth_Management_Company_Micro_Frontends_Development)

prs.save()
```

### Technology-Focused Deck

```python
from simple_deck import EPAMPresentation, CaseStudy

prs = EPAMPresentation("output/modern_architecture.pptx")

# Cloud modernization
prs.add_section_header_slide(
    section_title="Cloud Transformation",
    section_number="01",
)
prs.add_case_study(CaseStudy.Enterprise_Claims_System_Modernization_on_AWS)

# AI/ML capabilities
prs.add_section_header_slide(
    section_title="AI & Data Modernization",
    section_number="02",
)
prs.add_case_study(CaseStudy.Agentic_Data_Modernization)
prs.add_case_study(CaseStudy.Transforming_Healthcare_Claims_Analytics_with_Data_Pipeline_Modernization)

# Modern architecture
prs.add_section_header_slide(
    section_title="Enterprise Architecture",
    section_number="03",
)
prs.add_case_study(CaseStudy.Large_Wealth_Management_Company_Micro_Frontends_Development)

prs.save()
```

---

## Quick Reference

| Case Study | Industry | Focus Area | Key Technologies |
|------------|----------|------------|------------------|
| **Enterprise Claims AWS** | Insurance | Legacy Modernization | AWS, Mainframe, Cloud Migration |
| **Agentic Data Modernization** | Healthcare | AI/ML, Autonomous Agents | Agentic AI, Data Platforms |
| **Healthcare Claims Analytics** | Digital Health | Data Engineering | Databricks, Kafka, Analytics |
| **Wealth Management MFE** | Financial Services | Frontend Architecture | Micro-Frontends, DevOps |

---

## Notes

- All case studies are single-slide presentations
- Slides are read-only and cannot be modified programmatically
- Content is professionally designed with real client engagement details
- Use appropriate case studies based on target audience and industry focus
- Mix case studies with catalog slides for comprehensive presentations

---

=== Design tips ===
They are mostly irrelevant for outline creation, but please consider them when suggesting anything about layout.

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



---

=== ICONS LIBRARY ===

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

## Icons by categories
## Available Icons by Category

### Advanced_Technology

5g
Agility
CMS
Cloud_1
Hierarchy_Chart
Integration
IoT_1
Security
Server
Technology

### AI_VR_Bot

3D_Cube
3D_Cube_Bounce
AI_Humanbot
Android
Brain_1
Brain_2
Brain_3
Brain_Power
Chip
Computer
Digital_Engagement
Energy
Eye
Eye_Retina_Scanner
Fingerprint_Scanner
Flowchart
Gear
Head_AI
Head_Chip
Head_Gear
Head_Lock_1
Head_Lock_2
Head_Star
Head_Technology
Heads_Bubbles
Knowledge
Lightning
Machine_Learning
Manufacture
Network
Outlet
Pathways
Robot_1
Robot_2
Robot_Chat
Robotic
VR_Headset_1
VR_Headset_2
VR_Headset_3

### Arrows

Arrow_Circle_1
Arrow_Circle_2
Arrow_Circle_3
Arrows_Expand_1
Arrows_Expand_2
Arrows_Repeat_1
Arrows_Repeat_2
Arrows_Separate_1
Arrows_Separate_2
Arrows_Shuffle
Circulate_1
Circulate_2
Connection

### Buildings_Construction

Blueprint_1
Blueprint_2
Bricks
Building_Arrows
Building_Complex
Building_Dome
Building_Gear
Building_Height
Building_Permit
Building_Width
Buildings_City
Buildings_Towers
Crane_Containers
Crane_Wrecking_Ball
Drafting_Compass
Drafting_Triangle_Ruler
Factory_3
Hammer_Screwdriver
Hand_Building
Measuring_Tape
Oil_Pumpjack
Storefront

### Business

Alignment
Business_Transformation
Chart_Business
Consume
Delivery
Fraud
Gavel
Increase_Conversion
Pen_Contract
Person
Puzzle
Scale
Teamwork
Webpage

### Calendars_Timing

Calendar_2
Calendar_Alert
Calendar_Day_Star
Calendar_Gear
Calendar_Hourglass
Calendar_Month
Calendar_Pencil
Calendar_Refresh
Calendar_Search
Calendar_Star
Calendar_Sync
Clock_24h
Contact_Card

### Checkmarks

Checkmark_Box_1
Checkmark_Box_2
Checkmark_Circle_1
Checkmark_Circle_2
X_Mark_Box_1
X_Mark_Box_2
X_Mark_Circle_1

### Cloud

Cloud_3
Cloud_4
Cloud_Arrows_1
Cloud_Arrows_2
Cloud_Computing_1
Cloud_Computing_2
Cloud_Lightbulb

### Community

Book_Code_Of_Conduct
Code_For_Good
Community_Heart
Disrupt_For_Good
Empower
Hackathon
Human_Rights
Leader
Lightbulb_CSR
Lightbulb_Innovate_For_Good
Match_Ignite
Sharing_Knowledge
Stakeholders
Togetherness

### Core_Engineering

Cloud_2
Coding
Design_1
Design_2
Document_Checklist
Dot_Net
Engineering_Excellence
Java
Server
WWW

### Data_Analytics_Charts

Bar_Circle_Chart
Bar_Graph_2
Bar_Graph_Computer_1
Bar_Graph_Computer_2
Bar_Graph_Computer_3
Bar_Graph_Data
Bar_Graph_Gear
Bar_Graph_Increasing_1
Bar_Graph_Increasing_2
Bar_Graph_Magnify
Bar_Graph_Money
Bar_Graph_Variation
Document_Bar_Graph
Document_Chart_Graph
Document_Data_Magnify
Magnify_Data
Math_Symbols
Phone_Bar_Graph
Server_Graph_1
Server_Graph_2

### Data_Analysis

Bar_Graph
Brain_Power
Calculator
Clipboard
Cloud_2
Computer
Data
Document_Test
Facts
Funnel
Gear
Graph_Decreasing
Graph_Increasing
Lab_Security
Line_Graph
Path_Milestones
Server
Teacher

### Database

Database
Database_Alert
Database_Arrows
Database_Checkmark
Database_Connection
Database_Gear
Database_Magnify
Database_Network
Database_Sync
Server_Computers
Server_Connection
Server_Documents
Server_Laptop_Setup
Server_Tower

### Design_Marketing

AB_Testing
Computer_Arrow
Computer_Pencil_Gear
Computer_Pencil_Ruler
Drawing_Tablet
Mountain_Picture
Paint_Brush
Pencil_Drawing
Pencil_Gear
Pencil_Shapes
Pencil_Triangle
Pencil_Writing
SEO_Tag

### Devices_Media

Camera
Computer_Code
Computer_Document_Chart
Computer_Document_Checklist
Computer_Website
Computer_Wrench
Desktop_Computer
Device_Screen_Sizes
Fax_Machine
Film_Projector
Hand_Phone
Headphones
Internet_Router
Keyboard
Keyboard_USB
Keypad
Laptop
Laptop_Computer_Analytics
Laptop_Computer_Wrench
Media_Library
Media_Streaming
Phone
Phone_Handset
Phone_Handset_Arrows
Phone_Handset_Disconnect
Phone_Handset_Talk
Phone_Heart
Phone_Technology
Phone_Wrench
Phones_Gear_Arrows
Projector
Scanner
Smartwatch_Code
Tablet

### Digital_Engagement

Applied_Insights
Computer_New_Role
Digital_Banking
Digital_Engagement
Experience_Strategy
Instant_Payment
Service_Design
Shopping_Cart
Social
Strategy_Experience

### Documents

Book_2
Bookmark
Box
Briefcase_Box
Clipboard_Checkmark
DOC
Document_Checklist_2
Document_Layout
Document_Note
Folder
Folder_Connected
Folder_Contents
Folder_Open
Identification
Layers
MS_Excel
MS_Office
MS_Word
PDF
XLS

### Education

Book_Impact
Book_Open
Books
Brain_Pencil
Code_For_Good
Commitment_For_Good
Education
Education_Platforms
Empathy
Empower
Feather_Quill
Global
Governance
Guidance
Hand_Apple
Hand_Heart
Hand_Pencil
Handshake
Head_Brain
Head_Lightbulb
Knowledge
Lightbulb_CSR
Lightbulb_Innovate
Paper_Airplane
People_Double_Arrow
People_Talking
Person_Global
Person_Talking
Plant_Environment
Presentation_Chart
Presentation_Lectern
Presentation_Virtual
Shield_Insurance
Speech_Bubble_Round
Speech_Bubble_Square
Teacher
Teamwork
Values
Volunteers
Women

### Engineer

Cross_Device
Data_Funnel
Data_Transfer
Document_Refresh
Engineer_Laptop
Gear
Gear_AI_Pattern
Gear_BrainProcess
Gear_Circulating
Gear_Implementable
Gear_Moving
Global_Work
Hierarchy_Chart
Isometric_Steps
Phone_Gears
Positive_Negative
Process_Selection
Shopping_Cart_Gear

### Environment

Airplane
Book_Green_Education
Carbon_Footprint
Clean_Energy
Clean_Technology
Climate_Action
Climate_Change
Conservation
Green_Initiatives
Lightbulb_CSR
Plant_Environment
Recycle
Reduce
Renewable
Reuse
Sustainable_Procurement

### EPAM_Values_and_Personalities

Award_Original
Award_Trophy
Driven
Expert
Governance
Hand_Heart
Lightbulb_Innovate
Search
Teamwork
Values

### Everyday

Backpack
Book
Glasses
Movie_Voucher
Popcorn
Powerbank
Smartwatch
Speaker
Umbrella
Visa_Process
Water_Bottle

### Finance

ATM
Bank
Bitcoin
Bitcoin_Global
Cash_Calculator_Document
Cash_Money_1
Cash_Money_2
Check
Credit_Card_1
Credit_Card_2
Credit_Card_Cloud
Currency_Exchange
Diamond
Dollar
Ecommerce
Euro
Gold_Bars
Hand_Credit_Card
Lightbulb_Money
Money_Arrows_1
Money_Arrows_2
Money_Bag
Money_Certificate
Money_Magnify
Money_Moving
Payment_Terminal
Phone_Credit_Card
Phone_Mobile_Payment
Phone_NFC
Piggy_Bank
Pound
Receipt
Vault_Safe
Wallet
Yen

### Global

Global_Box
Global_Checkmarks
Global_Cloud
Global_People
Global_Search
Hand_Global
Hands_Global
Megaphone_Global
Phone_Global

### Goals

Arm_Muscles
Award_Laurel_1
Award_Ribbon
Badge_Award
Certificate
Chess_King
Flag
Hand_Dumbbell
High_Five
People_Stars_Above
People_Steps_Flag
Person_Stars_Above
Person_Stars_Below
Person_Steps_Flag
Podium
Star
Target
Thumbs_Up

### Help

Donation_Box
Hands_Money_Coin
Hands_Paw
Handshake_Globe
People_Heart_Arrows
Person_Heart
Plant_Person
Together_Globe

### Industry_Solutions

Clean_Energy
Credit_Card
Document
Healthcare_Stethoscope
Life_Sciences
Media_Entertainment_Telecom
Retail_Consumer
Shield_Insurance
Software_HiTech
Software_Hitech
Travel_Hospitality
Wheel_Automotive

### Intelligent_Enterprise

Bar_Graph
Maze_Business_Intelligence
Server
Teamwork

### Life_Sciences_Healthcare

Ambulance
Beaker_Stethoscope
Beakers
Clipboard_Health
DNA_Genetic_Testing
Data
Doctor
Doctor_Head_Mirror
Doctor_Laptop
Doctor_Patient
First_Aid_Kit
First_Aid_Kit_Money
Hand_Health_Cross
Hand_Health_Heart
Hand_Washing
Hands_Heartbeat
Healthcare_Drugs
Healthcare_Patient
Healthcare_Stethoscope
Heart_Monitor_1
Heart_Monitor_2
Heartbeat
Hospital
MRI_CT_Machine_1
MRI_CT_Machine_2
Molecule_1
Molecule_2
Passport_First_Aid_Kit
Petri_Dish
Pill_Bottle_1
Pill_Bottle_2
Pill_Bottle_3
Pills_Medication
Scientist
Smartwatch_Health
Test_Tubes_1
Test_Tubes_2
Thermometer_Health
Ultrasound
Xray

### Office

Award_Excellence
Career_Development
Checkmark
Clipboard
Clock
Document_Online_Request
House
Lightbulb
Location
Microphone
Teamwork

### People

2_People
3_People_1
3_People_2
3_People_3
Badge_Alert
Badge_Checkmark
Hands_Circle
Head_Brain_2
Identification_2
Lightbulb_Person
Magnify_Person
Notification_Person
People_Arrow_1
People_Arrow_2
People_Circle_1
People_Circle_2
People_Clock
People_Connection
People_Friends
People_Global
People_Message_Gear
People_Square
People_Table_1
People_Table_2
Person_Arrow_1
Person_Arrow_2
Person_Box
Person_Checklist_1
Person_Checklist_2
Person_Checkmark
Person_Circle_1
Person_Circle_2
Person_Cloud
Person_Gear
Person_Hard_Hat_Gear
Person_Hard_Hat_Wrench
Person_Hourglass
Person_Laptop_Headphones
Person_Laptop_Question
Person_Lectern_Global
Person_Location_Pin
Person_Magnify
Person_Message
Person_Pencil
Person_Suit
Person_Wrench

### Processes

2_Gears
3_Gears_1
3_Gears_2
API_1
API_2
Arrow_Save_Download
Arrows_Expand_3
Barcode
Chip_Arrow
Control_Block
Funnel_Chart
Funnel_Document
Funnel_Shapes
Gear_Arrows
Gear_Circle
Gear_Code
Gear_Factory
Gear_Process_1
Gear_Process_2
Gear_Process_3
Gear_Process_4
Gear_Process_5
Gear_Process_6
Hexagon_Arrows
Lightbulb_Gear_1
Lightbulb_Gear_2
Lightbulb_Gear_3
PMML
Process_1
Process_2
Process_3
Process_4
Process_5
Process_6
Puzzle_3

### Retail

Computer_Shopping_Bag
Laptop_Computer_Person_Support
Person_Dollars_Money
Person_Shopping_Bags
Person_Shopping_Cart
Phone_Shopping_Cart
Sale_Sticker
Shopping_Bag
Shopping_Bags
Shopping_Basket
Store_Location_Pin

### Strategy

Briefcase
Buildings
Castle
Coins
Dial
Hierarchy
Hourglass
Leader
Lightbulb
Location
Message
Microscope
News
Package
Pinpoint
Power
Presentation
Puzzle
Rocket
Shop
Strategy
Teamwork
Watch
Webpage

### Structures_Architectures

Diagram_1
Diagram_2
Document_Flowchart_Magnify
Flowchart_2
Flowchart_3
Flowchart_4
Gear_Diagram
Gear_Structure
Puzzle_2
Structure_1
Structure_2
Structure_3
Structure_4
Structure_5

### Tasks

Document_Checklist_3
Document_Checklist_4
Document_Checkmark
Document_Diagram
Document_Magnify
Document_Person
Document_Structure
Link

### Transport_Travel

Airplane_Clock
Airplane_Ticket
Bicycle
Bus_Car
Car_1
Car_2
Car_3
Car_4
Dump_Truck
Forklift
Taxi
Truck_1
Truck_2
Truck_3
Truck_4
Truck_5

### Web

Audio
Calendar
Computer_Filter_Content
Document_Online_Request
Envelope
Envelope_Automate_Newsletter
Envelope_Notifications
Gear_Automate
Global
Image_Missing
News
Play
Print
Search
Share
Webpage_2
Webpage_Diagram
Webpage_HTTP
Webpage_Pencil
Webpage_Person
Webpage_Windows_1
Webpage_Windows_2
Window_CRM
Window_Code
Window_Gear
Window_Lock

### Workshop

Clipboard
Clock
Document_Manufacture_Notes
Mallet
Manufacture
Paperclip
Phone_Digital_Tools
Repair
Repair_Process
Thumbtack
Tie
Truck_Work_Delivery
WorkPlan
WorkProcess


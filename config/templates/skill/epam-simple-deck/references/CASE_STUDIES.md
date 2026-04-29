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

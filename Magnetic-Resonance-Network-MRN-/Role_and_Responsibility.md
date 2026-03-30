# Magnetic Resonance Network (MRN)
## Roles, Responsibilities & Boundaries Matrix
Version: 3.3  
Sprint: Alpha  
Methodology: Agile Scrum  

---

# Team Role Assignment Table

| Role | Assigned To | Primary Responsibilities | Secondary Responsibilities | Decision Authority | Reporting To |
|------|------------|--------------------------|----------------------------|-------------------|--------------|
| Architect | นางสาวอลิชา ชนะบุญ | ออกแบบสถาปัตยกรรมทั้งระบบ กำหนด Layer และ Interface | ตรวจสอบความสอดคล้องของทุกองค์ประกอบ | อำนาจตัดสินใจสูงสุดด้าน Architecture | Instructor |
| Engineer | นายปรีชา ศรหีนองบัว | พัฒนา Routing Logic, Node Simulation และ Integration | ปรับปรุงประสิทธิภาพระบบ | ตัดสินใจด้าน Implementation | Architect |
| Specialist | นางสาวกัญญาภัค ทองวิเศษ | วิเคราะห์เชิงลึกด้าน Routing Strategy และ Optimization | สนับสนุนการออกแบบเชิงเทคนิคขั้นสูง | ให้คำปรึกษาเชิงเทคนิคเฉพาะทาง | Architect |
| DevOps | นายบุญปวณี เรืองไพศาล | จัดการ Deployment, CI/CD และ Environment Setup | ดูแลโครงสร้าง Repository และ Automation | ควบคุมกระบวนการ Release | Architect |
| Tester/QA | นางสาวปวริศา สดีดาชมภู | วางแผนทดสอบ เขียน Test Case และประเมิน KPI | ตรวจสอบเอกสารและคุณภาพ Sprint | อนุมัติความพร้อมก่อน Release | All |

---

# Detailed Responsibility Matrix
## By Project Phase

| Phase | Architect | Engineer | Specialist | DevOps | Tester/QA |
|-------|----------|----------|------------|--------|-----------|
| Week 1: Foundation | กำหนดโครงสร้างระบบและทำ Diagram | ตั้งโครง Simulation | วิเคราะห์ Routing Model | ตั้งค่า Repository | จัดทำ Test Plan |
| Week 2: Implementation | ตรวจสอบให้ตรงสเปก | พัฒนา Core Logic | ปรับปรุง Algorithm | เตรียม CI/CD | เขียน Unit Test |
| Week 3: Integration | ตรวจสอบความสอดคล้อง | รวมระบบ | วิเคราะห์ Performance | Deploy ทดสอบ | ทำ Integration Test |
| Week 4: Delivery | Review สถาปัตยกรรม | เตรียม Demo | Optimization ขั้นสุดท้าย | เตรียม Release Pipeline | Final Test & Report |

---

# Responsibility Area Matrix
## By Component

| Component | Design Owner | Implementation Owner | Testing Owner | Documentation Owner |
|-----------|--------------|---------------------|--------------|--------------------|
| System Architecture | Architect | Engineer | QA | Specialist |
| Routing Protocol | Architect | Engineer | QA | Specialist |
| Node Simulation | Architect | Engineer | QA | Engineer |
| CI/CD & Deployment | Architect | DevOps | QA | DevOps |
| Documentation | All | Engineer | QA | QA |

---

# Decision Authority Matrix

| Decision Type | Architect | Engineer | Specialist | DevOps | QA | Instructor |
|---------------|----------|----------|------------|--------|----|------------|
| Architecture Changes | Approve | Consult | Consult | Consult | Consult | Final if disputed |
| Algorithm Update | Approve | Propose | Consult | - | Consult | - |
| Implementation Approach | Review | Approve | Consult | - | - | - |
| Deployment Strategy | Review | Consult | - | Approve | - | - |
| Release Readiness | Consult | Consult | Consult | Consult | Approve | Final |

คำอธิบาย:  
- Approve = ผู้ตัดสินใจหลัก  
- Consult = ต้องมีการปรึกษาหารือ  
- Review = ตรวจสอบความถูกต้อง  

---

# Communication Boundaries

## Internal Communication Matrix

| From \ To | Architect | Engineer | Specialist | DevOps | QA |
|------------|----------|----------|------------|--------|----|
| Architect | - | แจ้ง Architecture Spec | ขอคำปรึกษาเชิงลึก | แจ้งแผน Deploy | แจ้งเกณฑ์ตรวจสอบ |
| Engineer | รายงานสถานะโค้ด | - | ขอคำแนะนำ Algorithm | แจ้ง Dependency | แจ้งความพร้อมทดสอบ |
| Specialist | เสนอ Optimization | ส่งข้อเสนอ Routing | - | แจ้งผลวิเคราะห์ | แจ้งผลวิเคราะห์ |
| DevOps | แจ้งสถานะ Deployment | แจ้ง Pipeline | - | - | แจ้งผลทดสอบระบบ |
| QA | รายงาน Bug | แจ้ง Coverage | แจ้งประเด็นคุณภาพ | แจ้งปัญหา Release | - |

---

# Escalation Path

พบปัญหา  
↓  
เจ้าของงานพยายามแก้ไข (ภายใน 6 ชั่วโมงทำงานจริง)  
↓  
แจ้ง Architect เพื่อพิจารณา (ภายใน 12 ชั่วโมง)  
↓  
ประชุมทีมเพื่อหาข้อสรุป (ภายใน 24 ชั่วโมง)  
↓  
หากยังไม่สามารถแก้ไขได้ → แจ้ง Instructor  

---

# Boundaries of Responsibility

## Architect
ทำ:
- ออกแบบโครงสร้างระบบ
- กำหนด Interface และมาตรฐาน
- ตัดสินใจด้านสถาปัตยกรรม
ไม่ทำ:
- เขียนโค้ดรายบรรทัดแทน Engineer

## Engineer
ทำ:
- พัฒนา Core Logic และ Simulation
- รวมระบบและแก้ปัญหาเชิงเทคนิค
ไม่ทำ:
- เปลี่ยน Architecture โดยไม่ผ่าน Architect

## Specialist
ทำ:
- วิเคราะห์และปรับปรุง Algorithm
- เสนอแนวทาง Optimization
ไม่ทำ:
- ตัดสินใจ Release หรือ Deployment

## DevOps
ทำ:
- ดูแล CI/CD
- ควบคุมกระบวนการ Deploy
ไม่ทำ:
- เปลี่ยน Algorithm ภายในระบบ

## Tester/QA
ทำ:
- วาง Test Plan และ KPI
- อนุมัติคุณภาพก่อน Release
ไม่ทำ:
- ตัดสินใจเชิงสถาปัตยกรรม

---

# RACI Matrix

| Activity | Architect | Engineer | Specialist | DevOps | QA |
|----------|----------|----------|------------|--------|----|
| Architecture Definition | R/A | C | C | I | I |
| Routing Implementation | C | R/A | C | I | I |
| Algorithm Optimization | C | C | R/A | I | I |
| CI/CD Setup | I | C | I | R/A | C |
| Integration Testing | C | C | C | C | R/A |

Legend:  
- R = Responsible  
- A = Accountable  
- C = Consulted  
- I = Informed  

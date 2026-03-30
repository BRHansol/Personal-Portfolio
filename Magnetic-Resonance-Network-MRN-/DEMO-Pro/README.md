## MRN Demo (Terminal Network Simulation)

เดโม่นี้จำลอง **Magnetic Resonance Network (MRN)** แบบง่าย ๆ บนคอมพิวเตอร์ โดยใช้หลายเทอร์มินัลแทนโหนดในเครือข่าย

- ทุกโหนดสามารถเป็น **ผู้ส่ง / ผู้รับ / ตัวกลาง** ได้
- Router จำลอง **ลิงก์สนามแม่เหล็ก** ที่มีข้อจำกัดเรื่องระยะทาง (ต้องใช้ตัวกลางช่วยถ้าไกลเกิน)
- มี **auto-routing** เลือกเส้นทางหลาย hop ตามตำแหน่งของโหนด
- มี **pytest** สำหรับทดสอบ logic การเลือกเส้นทางอัตโนมัติ

โค้ดหลักอยู่ที่ `mrn_terminal_node.py`

---

## 1. เตรียมสภาพแวดล้อม

รันคำสั่งทั้งหมดจากโฟลเดอร์ `DEMO-Pro`:

```bash
cd Magnetic-Resonance-Network-MRN-/DEMO-Pro
```

แนะนำให้ติดตั้ง `pytest` ไว้ด้วย (สำหรับ automated test):

```bash
python3 -m pip install pytest
```

---

## 2. รันเดโม่ 4 โหนด + 1 Router

### 2.1 เปิด Router (เทอร์มินัลที่ 1)

ในเทอร์มินัลอันแรก (ค้างไว้ ไม่ต้องปิด):

```bash
python3 "mrn_terminal_node.py" router --name MRN --port 7001 \
  --max-hop-distance 60 --max-hops 4 \
  --delay-ms 5 --delay-jitter-ms 5 \
  --distance-delay-ms-per-unit 3.0 \
  --loss 0.0 --loss-at-max 0.005
```

ความหมายแบบง่าย ๆ:
- **max-hop-distance**: ระยะสูงสุดต่อ 1 hop (เกินนี้ต้องมีตัวกลาง)
- **max-hops**: จำนวน hop สูงสุดที่ยอมให้มี
- **delay-ms / delay-jitter-ms**: ดีเลย์พื้นฐานต่อ hop
- **distance-delay-ms-per-unit**: ยิ่งไกลยิ่งช้าขึ้นแบบ non‑linear
- **loss / loss-at-max**: โอกาสแพ็กเก็ตหล่น โดยไกลมากจะหล่นมากขึ้น

### 2.2 เปิดโหนดทั้ง 4 ตัว (เทอร์มินัลที่ 2–5)

เปิดเทอร์มินัลใหม่ทีละอัน แล้วรันคำสั่งเหล่านี้ (อยู่ใน `DEMO-Pro` เหมือนกัน):

```bash
# เทอร์มินัลที่ 2
python3 "mrn_terminal_node.py" client --name N12 --host 127.0.0.1 --port 7001 --pos 0 0

# เทอร์มินัลที่ 3
python3 "mrn_terminal_node.py" client --name N34 --host 127.0.0.1 --port 7001 --pos 50 0

# เทอร์มินัลที่ 4
python3 "mrn_terminal_node.py" client --name N36 --host 127.0.0.1 --port 7001 --pos 100 0

# เทอร์มินัลที่ 5
python3 "mrn_terminal_node.py" client --name N58 --host 127.0.0.1 --port 7001 --pos 150 0
```

ค่าสมมติ:
- N12 = โหนดต้นทาง
- N58 = โหนดปลายทาง
- N34, N36 = ตัวกลางที่ช่วยแบ่งระยะ (multi‑hop)

---

## 3. วิธีส่งข้อความในเดโม่

ไปที่เทอร์มินัลของโหนดใดก็ได้ (เช่น `N12`) แล้วพิมพ์:

```text
/to N58 hello from N12
```

ผลลัพธ์:
- เทอร์มินัล `N58` จะแสดง:
  - `[RECV] N12 -> N58 | via N12 -> MRN -> N34 -> MRN -> N36 -> MRN -> N58 | age ... | ...`
- เทอร์มินัล `N34` / `N36` (ตัวกลาง) จะแสดง:
  - `[RELAY] forwarded to ...`

หมายเหตุ:
- `via` แสดงเส้นทางจริงที่แพ็กเก็ตเดินผ่าน
- `age` (ms) คือเวลาเดินทางตั้งแต่ส่งจนถึงจุดที่แสดงผล

---

## 4. ทดลอง “โหนดหายไป แต่เครือข่ายยังส่งต่อได้”

1. ปิดเทอร์มินัลของ `N36` (จำลองว่าโหนดกลางล้ม)
2. จาก `N12` ลองส่งอีกครั้ง:

   ```text
   /to N58 N36 is down test
   ```

3. สังเกต:
   - Router จะพยายามหาเส้นทางใหม่ตาม `max-hop-distance` และตำแหน่ง `/pos`
   - ถ้ายังมีทางผ่าน N34 ตรง ๆ ถึง N58 ได้ (ไม่เกินระยะ) เส้นทางอาจเปลี่ยนเป็น:
     - `N12 -> MRN -> N34 -> MRN -> N58`

4. เปิด `N36` ใหม่ด้วยคำสั่งเดิม (พร้อม `--pos 100 0`) แล้วลองส่งอีกครั้งเพื่อดูว่าเส้นทางกลับมาผ่าน N34,N36 ครบหรือไม่

---

## 5. Automated Test ด้วย pytest

มีตัวอย่างเทสต์ใน `tests/test_mrn_routing.py` สำหรับตรวจสอบ logic การเลือกเส้นทาง:

```python
from mrn_terminal_node import compute_auto_path, NodeInfo

def test_auto_path_prefers_multi_hop():
    nodes = {
        "N12": NodeInfo("N12", writer=None, pos=(0.0, 0.0)),
        "N34": NodeInfo("N34", writer=None, pos=(50.0, 0.0)),
        "N36": NodeInfo("N36", writer=None, pos=(100.0, 0.0)),
        "N58": NodeInfo("N58", writer=None, pos=(150.0, 0.0)),
    }

    path = compute_auto_path("N12", "N58", nodes, max_hop_distance=60.0, max_hops=4)

    # เส้นทางที่คาดหวัง: ต้องใช้ตัวกลางทั้ง N34 และ N36
    assert path == ["N12", "N34", "N36", "N58"]
```

รันเทสต์ทั้งหมด:

```bash
cd Magnetic-Resonance-Network-MRN-/DEMO-Pro
python3 -m pytest
```

ผลลัพธ์ที่คาดหวัง:
- `1 passed` แสดงว่า logic auto‑routing เลือกเส้นทางผ่านตัวกลางตามสเปคที่กำหนด

---

## 6. ไอเดียต่อยอดสำหรับรายงาน/เดโม่

- วัดค่า `age` เฉลี่ย สำหรับ:
  - ส่งตรง (เมื่อระยะไม่ไกลมาก)
  - ส่งแบบ multi‑hop ผ่าน N34,N36
- ทดลองปิด/เปิดตัวกลาง แล้วเปรียบเทียบเส้นทางกับค่า latency
- ใช้ตารางเปรียบเทียบ:
  - จำนวน hop
  - `age` เฉลี่ย
  - จำนวนแพ็กเก็ตที่ถึง / หล่น

ทั้งหมดนี้สามารถใส่ในรายงานในส่วน “การทดลอง” และ “การประเมินผล MRN” ได้ตรง ๆ


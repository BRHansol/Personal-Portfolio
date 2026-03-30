import pytest

from mrn_terminal_node import compute_auto_path, NodeInfo

def test_auto_path_prefers_multi_hop():
    # สร้างโหนดจำลอง + ตำแหน่ง
    nodes = {
        "N12": NodeInfo("N12", writer=None, pos=(0.0, 0.0)),
        "N34": NodeInfo("N34", writer=None, pos=(50.0, 0.0)),
        "N36": NodeInfo("N36", writer=None, pos=(100.0, 0.0)),
        "N58": NodeInfo("N58", writer=None, pos=(150.0, 0.0)),
    }

    path = compute_auto_path("N12", "N58", nodes, max_hop_distance=60.0, max_hops=4)

    # คาดหวังว่า router จะเลือกเส้นทางหลาย hop ผ่าน N34,N36
    assert path == ["N12", "N34", "N36", "N58"]
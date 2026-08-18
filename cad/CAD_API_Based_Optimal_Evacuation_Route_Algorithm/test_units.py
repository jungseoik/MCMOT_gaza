"""도면 단위($INSUNITS) 처리 검증."""
import sys


import ezdxf
from evac import cad

DXF = ("/home/pia/seoik/MCMOT_gaza/cad/"
       "CAD_API_Based_Optimal_Evacuation_Route_Algorithm/Egress_Review-Test.dxf")

print("① 실제 도면 단위 판독")
doc = ezdxf.readfile(DXF)
code, mm, name = cad.read_units(doc)
print(f"   $INSUNITS={code} · 계수={mm} · 이름={name}")
assert (code, mm) == (4, 1.0)

print("\n② 단위별 환산 계수")
for c, expect in ((1, 25.4), (2, 304.8), (4, 1.0), (5, 10.0), (6, 1000.0)):
    doc.header["$INSUNITS"] = c
    _, f, nm = cad.read_units(doc)
    ok = "✔" if f == expect else "✗"
    print(f"   {ok} 코드 {c} ({nm:4}) → {f} mm")
    assert f == expect

print("\n③ 미지정(0) — 계수 None 이어야 사용자에게 물을 수 있다")
doc.header["$INSUNITS"] = 0
_, f, nm = cad.read_units(doc)
print(f"   코드 0 ({nm}) → 계수={f}")
assert f is None, "미지정은 None 이어야 한다(임의 mm 가정 금지)"

print("\n④ 헤더 자체가 없는 경우")
class FakeDoc:
    header = {}
_, f, nm = cad.read_units(FakeDoc())
print(f"   헤더 없음 → 계수={f} ({nm})")
assert f is None

print("\n⑤ 축척 환산 검증 — 같은 도면이 단위만 다르면 m_per_px 도 비례")
W_PX = 2000
span = 124027.1        # 실제 도면 가로 범위(도면단위)
for c, mmf, label in ((4, 1.0, "mm"), (5, 10.0, "cm"), (6, 1000.0, "m")):
    m_per_px = span * mmf / 1000.0 / W_PX
    width_m = span * mmf / 1000.0
    print(f"   {label:3} → 건물 폭 {width_m:>10.1f} m · m_per_px {m_per_px:.5f}")

print("\n전부 통과")

#!/usr/bin/env python3
"""
evac_route.py — evac 패키지 CLI 얇은 진입점(하위호환용).

실제 로직은 evac/ 패키지에 있다(core/cad/render/pick/cli). 아래처럼 써도 되고
`python -m evac.cli ...` 로 직접 호출해도 동일하다.

  python evac_route.py route   --dxf Egress_Review-Test.dxf --out out.png --show-ref
  python evac_route.py pick    --dxf plan.dxf --n 2 --write-exits-dxf plan_tagged.dxf
  python evac_route.py connect --dxf 17F.dxf --out conn.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evac.cli import main   # noqa: E402

if __name__ == "__main__":
    main()

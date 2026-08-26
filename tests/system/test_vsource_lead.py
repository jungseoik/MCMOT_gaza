"""앞머리(정지화면) 길이 산정 — 고정 상수가 아니라 실측 부착시간에서 나온다."""
from system.vsource import controller as c


class TestLeadSeconds:
    def test_measured_attach_plus_margin(self):
        """실측이 있으면 그 값 + 여유. 카메라 복귀를 덮어야 하므로 반드시 더 크다."""
        assert c.lead_seconds(23.0) == 23.0 + c.LEAD_MARGIN
        assert c.lead_seconds(23.0) > 23.0

    def test_no_measurement_uses_default(self):
        for v in (None, 0, -1):
            assert c.lead_seconds(v) == c.LEAD_STILL_DEFAULT

    def test_clamped_to_range(self):
        """너무 짧으면 복귀를 못 덮고, 너무 길면 시연이 지루해진다."""
        assert c.lead_seconds(0.5) == c.LEAD_STILL_MIN
        assert c.lead_seconds(500) == c.LEAD_STILL_MAX

    def test_monotonic(self):
        """부착이 오래 걸릴수록 앞머리도 길어야 한다."""
        vals = [c.lead_seconds(x) for x in (5, 15, 25, 40, 60)]
        assert vals == sorted(vals)


class TestLeadCmd:
    def test_lead_uses_concat_without_reencode(self):
        """앞머리는 concat 데먹서 + copy — 재인코딩하면 채널당 CPU가 붙는다."""
        from system.vsource.publisher import ffmpeg_cmd
        cmd = ffmpeg_cmd("v.mp4", "rtsp://h/p", lead="lead.txt")
        assert "-f" in cmd and "concat" in cmd
        assert cmd[cmd.index("-i") + 1] == "lead.txt"
        assert "copy" in cmd
        assert "libx264" not in cmd

    def test_no_lead_keeps_plain_path(self):
        from system.vsource.publisher import ffmpeg_cmd
        cmd = ffmpeg_cmd("v.mp4", "rtsp://h/p")
        assert "concat" not in cmd
        assert cmd[cmd.index("-i") + 1] == "v.mp4"

    def test_re_flag_always_present(self):
        """-re 없이 보내면 181초를 몇 초에 쏟아부어 시간축이 무너진다."""
        from system.vsource.publisher import ffmpeg_cmd
        for lead in (None, "lead.txt"):
            assert "-re" in ffmpeg_cmd("v.mp4", "rtsp://h/p", lead=lead)

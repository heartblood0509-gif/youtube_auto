"""Ken Burns 모션 효과 - 이미지를 영상 클립으로 변환"""

import subprocess


def apply_ken_burns(
    image_path: str,
    output_path: str,
    motion_type: str,
    duration: float,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
):
    """
    이미지에 Ken Burns 효과(줌/팬)를 적용하여 영상 클립 생성.

    4배 업스케일 후 zoompan 적용 → 줌 시 화질 저하 방지.
    """
    total_frames = int(duration * fps)
    zoom_speed = 0.25 / total_frames  # 전체 25% 줌

    # 업스케일 해상도 (4배)
    up_w = width * 4
    up_h = height * 4

    filter_map = {
        "zoom_in": (
            f"zoompan=z='min(1+{zoom_speed}*on,1.25)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
        "zoom_out": (
            f"zoompan=z='max(1.25-{zoom_speed}*on,1.0)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
        "pan_left": (
            f"zoompan=z='1.15':"
            f"x='(iw-iw/zoom)*(1-on/{total_frames})':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
        "pan_right": (
            f"zoompan=z='1.15':"
            f"x='(iw-iw/zoom)*on/{total_frames}':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
        "pan_up": (
            f"zoompan=z='1.15':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*(1-on/{total_frames})':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
        "pan_down": (
            f"zoompan=z='1.15':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih-ih/zoom)*on/{total_frames}':"
            f"d={total_frames}:s={width}x{height}:fps={fps}"
        ),
    }

    zoompan_filter = filter_map.get(motion_type, filter_map["zoom_in"])
    vf = f"scale={up_w}:{up_h}:flags=lanczos,{zoompan_filter}"

    cmd = (
        f'ffmpeg -y -loop 1 -i "{image_path}" '
        f'-vf "{vf}" '
        f"-t {duration} "
        f"-c:v libx264 -preset fast -crf 18 "
        f"-pix_fmt yuv420p -r {fps} -an "
        f'"{output_path}"'
    )

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ken Burns 실패: {result.stderr[:500]}")

    return output_path

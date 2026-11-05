"""Render Lottie to APNG in a short-lived, bounded child process."""

import io
import json
import math
import multiprocessing

from PIL import Image

MAX_FILE = 512 * 1024


def render_lottie(data):
    from rlottie_python import LottieAnimation

    if len(data) > MAX_FILE:
        raise ValueError("The original Lottie file exceeds Discord’s sticker size limit.")
    try:
        document = json.loads(data)
        fps = float(document["fr"])
        start, end = float(document.get("ip", 0)), float(document["op"])
        width, height = int(document["w"]), int(document["h"])
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValueError("The source Lottie animation has invalid timing or dimensions.") from None
    if not all(math.isfinite(v) for v in (fps, start, end)) or not 0 < fps <= 240:
        raise ValueError("The source animation has an unsupported frame rate.")
    duration = (end - start) / fps
    if not 0 < duration <= 5 or not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError(
            "Discord stickers must be no longer than 5 seconds and use valid dimensions."
        )
    for asset in document.get("assets", []):
        if "p" in asset:
            # Never let native rendering follow filesystem or network image references.
            if asset.get("e") != 1 or not str(asset["p"]).startswith("data:image/"):
                raise ValueError(
                    "This animation depends on an external image and cannot be copied safely."
                )
    output_fps = min(fps, 60)
    count = max(2, int(math.ceil(duration * output_fps)))
    frames = []
    with LottieAnimation.from_data(data=json.dumps(document)) as animation:
        total = animation.lottie_animation_get_totalframe()
        if total < 2:
            raise ValueError("The source has no multi-frame animation to preserve.")
        for index in range(count):
            frame_num = min(total - 1, int(index * total / count))
            frames.append(
                animation.render_pillow_frame(frame_num=frame_num, width=320, height=320).convert(
                    "RGBA"
                )
            )
    try:
        # Try lossless color output first; if needed, reduce frame rate without shortening playback.
        for target_fps in (60, 30, 24, 20, 15, 10):
            target = min(count, max(2, int(math.ceil(duration * min(output_fps, target_fps)))))
            indices = list(dict.fromkeys(int(i * count / target) for i in range(target)))
            chosen = [frames[i] for i in indices]
            boundaries = [round(i * duration * 1000 / len(chosen)) for i in range(len(chosen) + 1)]
            timings = [b - a for a, b in zip(boundaries, boundaries[1:])]
            buffer = io.BytesIO()
            chosen[0].save(
                buffer,
                format="PNG",
                save_all=True,
                append_images=chosen[1:],
                duration=timings,
                loop=0,
                disposal=0,
                blend=0,
                optimize=True,
            )
            result = buffer.getvalue()
            if len(result) <= MAX_FILE:
                with Image.open(io.BytesIO(result)) as check:
                    if not check.is_animated or check.n_frames < 2:
                        raise ValueError("This animation could not be preserved as a moving APNG.")
                return result
        raise ValueError(
            "The animation cannot fit Discord’s 512 KiB sticker limit without "
            "losing its animation. No still image was uploaded."
        )
    finally:
        for frame in frames:
            frame.close()


def _worker(connection, data):
    try:
        connection.send_bytes(b"1" + render_lottie(data))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else "Lottie rendering failed."
        connection.send_bytes(b"0" + message[:500].encode("utf-8"))
    finally:
        connection.close()


def convert_lottie(data):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, data), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(40):
            raise ValueError("Animation conversion timed out. No still image was uploaded.")
        try:
            payload = receiver.recv_bytes(MAX_FILE + 1024)
        except (EOFError, OSError):
            raise ValueError(
                "The renderer could not convert this animation. Nothing was flattened."
            ) from None
        if payload[:1] != b"1":
            raise ValueError(payload[1:].decode("utf-8", errors="replace"))
        return payload[1:]
    finally:
        receiver.close()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)

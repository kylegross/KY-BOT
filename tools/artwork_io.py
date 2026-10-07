"""Replace generated artwork atomically so OneDrive never sees partially written images."""

import io
import uuid
from pathlib import Path


def write_artwork(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_artwork(image, path):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    write_artwork(path, stream.getvalue())

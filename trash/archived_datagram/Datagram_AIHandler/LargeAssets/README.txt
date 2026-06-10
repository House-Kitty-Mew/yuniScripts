# LargeAssets Directory
This folder is intended for bulky binary assets that are part of the datagram.

## Purpose
- Store images, audio, video, 3D models, or other large files.
- Keep the main database lean by storing only metadata and references.
- Enable streaming or progressive loading of assets.

## Guidelines
- Assets can be organized in subdirectories (e.g., `Images/`, `Audio/`).
- Refer to assets from the database using relative paths (e.g., `LargeAssets/Images/photo.jpg`).
- Consider compression (e.g., ZIP, brotli) for distribution, but keep the format openable without external tools.

## Example
If the GUI references an image, the database entry might contain:
```
{
  "id": "imgID_1",
  "path": "LargeAssets/Images/wonderful_image.png",
  "width": 1920,
  "height": 1080
}
```

## Notes
- This is a template directory; replace this README with your actual assets.
- Empty by default.

---
Datagram Specification v1.0.0
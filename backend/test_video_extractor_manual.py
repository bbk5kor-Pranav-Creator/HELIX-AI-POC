
from pathlib import Path
import json

from video_extractor import extract_video_knowledge

video_path = Path.home() / "Downloads" / "sample.mp4"

print(f"Processing: {video_path}")
print("This may take a while on the first run while models initialize.")

result = extract_video_knowledge(video_path)

print("\n--- PROCESSING SUMMARY ---")
print("Filename:", result["filename"])
print("Duration:", result["metadata"]["duration_seconds"])
print("Transcript segments:", len(result["transcript"]["segments"]))
print("Processing seconds:", result["processing_seconds"])
print("Knowledge characters:", len(result["knowledge_text"]))

output_path = Path("video_extraction_test_result.json")
output_path.write_text(
    json.dumps(result, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

print("\nSaved test result:", output_path.resolve())
print("\n--- KNOWLEDGE TEXT PREVIEW ---")
print(result["knowledge_text"][:3000])

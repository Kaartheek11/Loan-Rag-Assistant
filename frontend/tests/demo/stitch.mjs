// Cut each recorded scene from its actor's video and join them into one MP4 (ffmpeg).
//   node tests/demo/stitch.mjs  ->  docs/demo/loanlens-demo.mp4
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../..");
const work = resolve(repo, "frontend/test-results/demo-videos");
const { scenes, videos } = JSON.parse(readFileSync(resolve(work, "scenes.json"), "utf8"));
const parts = [];
scenes.forEach((s, i) => {
  const out = resolve(work, `part-${i}.mp4`);
  execFileSync("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-ss",
    String(Math.max(0, s.from)),
    "-to",
    String(s.to),
    "-i",
    videos[s.actor],
    "-vf",
    "fps=25,format=yuv420p",
    "-c:v",
    "libx264",
    "-crf",
    "28",
    "-preset",
    "veryfast",
    "-an",
    out,
  ]);
  parts.push(`file '${out}'`);
});
writeFileSync(resolve(work, "parts.txt"), parts.join("\n"));
mkdirSync(resolve(repo, "docs/demo"), { recursive: true });
const final = resolve(repo, "docs/demo/loanlens-demo.mp4");
execFileSync("ffmpeg", [
  "-y",
  "-loglevel",
  "error",
  "-f",
  "concat",
  "-safe",
  "0",
  "-i",
  resolve(work, "parts.txt"),
  "-c",
  "copy",
  final,
]);
const seconds = execFileSync(
  "ffprobe",
  ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", final],
  { encoding: "utf8" },
).trim();
console.log(`wrote ${final} (${Number(seconds).toFixed(0)} s)`);
scenes.forEach((s) =>
  console.log(`  ${s.actor.padEnd(6)} ${(s.to - s.from).toFixed(0).padStart(4)} s  ${s.title}`),
);

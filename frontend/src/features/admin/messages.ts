// UI-F18: failure messages, shown on the file row and in the uploads tray.
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_FILES = 20;

export function uploadFailureMessage(
  reason: string | null,
  extra?: { existing_filename?: string },
): string {
  switch (reason) {
    case "not_a_pdf":
      return "This file isn't a PDF.";
    case "encrypted_pdf":
      return "This PDF is password protected. Remove the password and upload it again.";
    case "too_many_pages":
      return "This PDF has more than 500 pages.";
    case "file_too_large":
      return "This file is larger than 50 MB.";
    case "duplicate":
      return `This file was already uploaded as ${extra?.existing_filename ?? "another file"}.`;
    case "invalid_access":
      return "Choose at least one group or person who can read this file.";
    case "unreadable_pdf":
      return "This PDF couldn't be read. Try exporting it again and re-uploading.";
    case "ocr_failed":
    case "no_text_found":
      return "No readable text was found in this PDF.";
    case "rate_limited":
      return "Too many uploads in the last hour. Try again later.";
    case "internal_error":
    default:
      return "Processing failed. Try again.";
  }
}

/** Browser-side convenience checks only; the backend decides (UI-F16). */
export function quickCheck(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "This file isn't a PDF.";
  if (file.size > MAX_UPLOAD_BYTES) return "This file is larger than 50 MB.";
  return null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

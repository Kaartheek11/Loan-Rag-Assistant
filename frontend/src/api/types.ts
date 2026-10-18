// Mirrors the backend response models (app/api/schemas.py).

export type Me = {
  user_id: string;
  display_name: string;
  groups: string[];
  roles: string[];
  readable_document_count: number;
};

export type MyDocument = {
  doc_id: string;
  filename: string;
  page_count: number;
  ingested_at: string;
};

export type MyDocumentsPage = { items: MyDocument[]; next_cursor: string | null };

export type Citation = {
  marker: number;
  doc_id: string;
  filename: string;
  page: number;
  excerpt: string;
};

export type QueryResponse = {
  request_id: string;
  status: "answered" | "insufficient";
  answer: string;
  citations: Citation[];
};

export type PageResponse = {
  doc_id: string;
  filename: string;
  page: number;
  text: string;
  page_count: number;
  section: string | null;
};

// ---- admin (extension.md BE-8 to BE-12) ----
export type UploadProgress = {
  job_id: string;
  status: "queued" | "processing" | "indexed" | "failed";
  pages_done: number;
  pages_total: number;
  failure_reason: string | null;
};

export type AdminDocument = {
  doc_id: string;
  filename: string;
  page_count: number;
  status: "pending" | "indexed" | "failed";
  failure_reason: string | null;
  groups: string[];
  users: string[];
  uploaded_by: string | null;
  ingested_at: string;
  upload: UploadProgress | null;
};

export type AdminDocumentsPage = {
  items: AdminDocument[];
  next_cursor: string | null;
  summary: { total: number; processing: number; failed: number };
};

export type UploadJob = {
  job_id: string;
  doc_id: string;
  filename: string;
  status: UploadProgress["status"];
  pages_done: number;
  pages_total: number;
  failure_reason: string | null;
  created_at: string;
  uploaded_by: string;
};

export type AdminUser = { user_id: string; display: string };

import { useRef, useState } from "react";
import { downloadImportTemplate, importItemsExcel } from "../../api/client";
import type { ImportPreview, ItemType } from "../../types";
import { downloadBlob } from "../../utils/download";

const PROFILE_LABELS: Record<ItemType, string> = {
  box: "Loose Boxes",
  pallet: "Palletized Load",
  panel: "Panels & Fragile",
  custom: "Custom Load",
};

interface Props {
  profile: ItemType;
  value: ImportPreview | null;
  onChange: (preview: ImportPreview | null) => void;
}

export function ImportStep({ profile, value, onChange }: Props) {
  const [downloading, setDownloading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleDownloadTemplate() {
    setDownloading(true);
    setError("");
    try {
      const blob = await downloadImportTemplate(profile);
      downloadBlob(blob, `cubox-import-template-${profile}.xlsx`);
    } catch {
      setError("Could not download the template. Please check your connection to the backend.");
    } finally {
      setDownloading(false);
    }
  }

  async function handleFileSelected(file: File) {
    setUploading(true);
    setError("");
    onChange(null);
    try {
      const preview = await importItemsExcel(file, profile);
      onChange(preview);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not read the Excel file. Please verify it is a valid .xlsx file.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <div className="wizard-step-body">
      <h2>Import Items</h2>
      <p className="step-subtitle">Download the {PROFILE_LABELS[profile]} template, fill it in, and upload it here.</p>

      {error && <div className="wizard-error-banner">{error}</div>}

      <div className="import-actions">
        <button type="button" className="btn-secondary" onClick={handleDownloadTemplate} disabled={downloading}>
          {downloading ? "Downloading…" : "Download CUBOX Template"}
        </button>
        <button type="button" className="btn-secondary" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
          {uploading ? "Reading file…" : "Upload Excel (.xlsx)"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xlsm"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFileSelected(file);
          }}
        />
      </div>

      {!value && !uploading && <div className="import-dropzone">No file imported yet.</div>}

      {value && <ImportPreviewSummary preview={value} />}
    </div>
  );
}

function ImportPreviewSummary({ preview }: { preview: ImportPreview }) {
  const { summary } = preview;
  return (
    <div>
      <h3 style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>
        Import Summary
      </h3>
      <div className="import-summary-grid">
        <div className="import-summary-tile">
          <div className="value">{summary.total_rows}</div>
          <div className="label">Rows</div>
        </div>
        <div className="import-summary-tile">
          <div className="value">{summary.total_units.toLocaleString()}</div>
          <div className="label">Units</div>
        </div>
        <div className="import-summary-tile">
          <div className="value">{summary.total_weight.toLocaleString()} kg</div>
          <div className="label">Total Weight</div>
        </div>
      </div>

      {preview.is_valid ? (
        <div className="import-status-banner ok">Ready to continue.</div>
      ) : (
        <div className="import-status-banner error">
          This Excel file needs correction before you can continue. Fix the issues below and upload it again.
        </div>
      )}

      {preview.warnings.length > 0 && (
        <>
          <h3 style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>
            Warnings
          </h3>
          <IssuesTable issues={preview.warnings} />
        </>
      )}

      {preview.errors.length > 0 && (
        <>
          <h3 style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>
            Issues
          </h3>
          <IssuesTable issues={preview.errors} />
        </>
      )}
    </div>
  );
}

function IssuesTable({ issues }: { issues: ImportPreview["errors"] }) {
  return (
    <table className="issues-table">
      <thead>
        <tr>
          <th>Row</th>
          <th>Column</th>
          <th>Issue</th>
        </tr>
      </thead>
      <tbody>
        {issues.map((issue, index) => (
          <tr key={index}>
            <td>{issue.row ?? "File"}</td>
            <td>{issue.column ?? "—"}</td>
            <td>{issue.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

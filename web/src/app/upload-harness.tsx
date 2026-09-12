"use client";

import { useState, type FormEvent } from "react";

import { MAX_STEP_UPLOAD_BYTES } from "@/domain/step-upload.ts";

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function responseJson(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.error ?? `request failed with ${response.status}`);
  return body;
}

export function UploadHarness() {
  const [modelName, setModelName] = useState("Development model");
  const [modelId, setModelId] = useState("");
  const [message, setMessage] = useState("Create a Model or enter an existing Model ID.");

  async function createModel(event: FormEvent) {
    event.preventDefault();
    try {
      const response = await fetch("/api/models", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: modelName }),
      });
      const model = await responseJson(response);
      setModelId(model.id);
      setMessage(`Model created: ${model.id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Model creation failed");
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("step");
    if (!(file instanceof File) || modelId.length === 0) {
      setMessage("A Model ID and STEP file are required.");
      return;
    }
    try {
      setMessage("Computing browser SHA-256…");
      const clientSha256 = await sha256(file);
      const request = await responseJson(await fetch(`/api/models/${modelId}/versions/uploads`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          originalFilename: file.name,
          sizeBytes: file.size,
          clientSha256,
        }),
      }));
      setMessage("Uploading directly to private object storage…");
      const put = await fetch(request.uploadUrl, {
        method: "PUT",
        headers: request.requiredHeaders,
        body: file,
      });
      if (!put.ok) throw new Error(`direct upload failed with ${put.status}`);
      const version = await responseJson(await fetch(
        `/api/models/${modelId}/versions/uploads/${request.uploadId}/confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ clientSha256 }),
        },
      ));
      setMessage(`ModelVersion ${version.versionNumber} finalized (${version.id}).`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    }
  }

  return (
    <section>
      <form onSubmit={createModel}>
        <label>
          Model name
          <input value={modelName} onChange={(event) => setModelName(event.target.value)} />
        </label>
        <button type="submit">Create Model</button>
      </form>
      <label>
        Existing/new Model ID
        <input value={modelId} onChange={(event) => setModelId(event.target.value)} />
      </label>
      <form onSubmit={upload}>
        <input name="step" type="file" accept=".step,.stp" required />
        <button type="submit">Upload STEP</button>
      </form>
      <p>V0 limit: {MAX_STEP_UPLOAD_BYTES / (1024 * 1024)} MiB. STEP bytes upload directly to R2.</p>
      <output>{message}</output>
    </section>
  );
}

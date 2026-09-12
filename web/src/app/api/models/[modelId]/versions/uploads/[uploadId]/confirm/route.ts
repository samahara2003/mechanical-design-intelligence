import { confirmModelVersionUpload } from "@/application/model-version-uploads.ts";
import { getDatabase } from "@/db/client.ts";
import { getPrivateObjectStorage } from "@/storage/r2.ts";

export async function POST(
  request: Request,
  context: { params: Promise<{ modelId: string; uploadId: string }> },
) {
  try {
    const { modelId, uploadId } = await context.params;
    const body = await request.json() as { clientSha256?: unknown };
    if (typeof body.clientSha256 !== "string") {
      return Response.json({ error: "clientSha256 is required" }, { status: 400 });
    }
    const version = await confirmModelVersionUpload(
      getDatabase(),
      getPrivateObjectStorage(),
      { modelId, uploadId, clientSha256: body.clientSha256 },
    );
    return Response.json({
      id: version.id,
      modelId: version.modelId,
      versionNumber: version.versionNumber,
      originalFilename: version.originalFilename,
      cadSha256: version.cadSha256,
      sourceSizeBytes: version.sourceSizeBytes,
    }, { status: 201 });
  } catch {
    return Response.json({ error: "upload confirmation rejected" }, { status: 400 });
  }
}

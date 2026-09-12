import { requestModelVersionUpload } from "@/application/model-version-uploads.ts";
import { getDatabase } from "@/db/client.ts";
import { getPrivateObjectStorage } from "@/storage/r2.ts";

export async function POST(
  request: Request,
  context: { params: Promise<{ modelId: string }> },
) {
  try {
    const { modelId } = await context.params;
    const body = await request.json() as {
      originalFilename?: unknown;
      sizeBytes?: unknown;
      clientSha256?: unknown;
    };
    if (
      typeof body.originalFilename !== "string"
      || typeof body.sizeBytes !== "number"
      || typeof body.clientSha256 !== "string"
    ) {
      return Response.json({ error: "invalid upload metadata" }, { status: 400 });
    }
    const upload = await requestModelVersionUpload(
      getDatabase(),
      getPrivateObjectStorage(),
      modelId,
      {
        originalFilename: body.originalFilename,
        sizeBytes: body.sizeBytes,
        clientSha256: body.clientSha256,
      },
    );
    return Response.json(upload, { status: 201 });
  } catch {
    return Response.json({ error: "upload request rejected" }, { status: 400 });
  }
}

import { createModel } from "@/application/persistence.ts";
import { getDatabase } from "@/db/client.ts";

export async function POST(request: Request) {
  try {
    const body = await request.json() as { name?: unknown };
    if (typeof body.name !== "string") return Response.json({ error: "name is required" }, { status: 400 });
    const model = await createModel(getDatabase(), body.name);
    return Response.json({ id: model.id, name: model.name }, { status: 201 });
  } catch {
    return Response.json({ error: "unable to create Model" }, { status: 400 });
  }
}

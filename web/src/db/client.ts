import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import * as schema from "./schema.ts";

let database: ReturnType<typeof drizzle<typeof schema>> | undefined;
let pool: Pool | undefined;

export function getDatabase() {
  if (database !== undefined) return database;
  const connectionString = process.env.DATABASE_URL;
  if (connectionString === undefined || connectionString.trim() === "") {
    throw new Error("DATABASE_URL is required to access PostgreSQL");
  }
  pool = new Pool({ connectionString });
  database = drizzle(pool, { schema });
  return database;
}

/** Development scripts can close their process-owned pool deterministically. */
export async function closeDatabase(): Promise<void> {
  if (pool !== undefined) await pool.end();
  pool = undefined;
  database = undefined;
}

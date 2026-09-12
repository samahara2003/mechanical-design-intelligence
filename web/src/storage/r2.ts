import {
  DeleteObjectCommand,
  GetObjectCommand,
  HeadObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

import type {
  PresignedPutRequest,
  PrivateObjectStorage,
  StoredObjectMetadata,
} from "./object-storage.ts";

interface R2Configuration {
  readonly accountId: string;
  readonly accessKeyId: string;
  readonly secretAccessKey: string;
  readonly bucketName: string;
}

function requireEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.trim() === "") throw new Error(`${name} is required`);
  return value;
}

export function r2ConfigurationFromEnvironment(): R2Configuration {
  return {
    accountId: requireEnvironment("R2_ACCOUNT_ID"),
    accessKeyId: requireEnvironment("R2_ACCESS_KEY_ID"),
    secretAccessKey: requireEnvironment("R2_SECRET_ACCESS_KEY"),
    bucketName: requireEnvironment("R2_BUCKET_NAME"),
  };
}

export class R2PrivateObjectStorage implements PrivateObjectStorage {
  readonly #client: S3Client;
  readonly #bucketName: string;

  constructor(configuration: R2Configuration) {
    this.#bucketName = configuration.bucketName;
    this.#client = new S3Client({
      region: "auto",
      endpoint: `https://${configuration.accountId}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId: configuration.accessKeyId,
        secretAccessKey: configuration.secretAccessKey,
      },
    });
  }

  async createPresignedPut(request: PresignedPutRequest) {
    const command = new PutObjectCommand({
      Bucket: this.#bucketName,
      Key: request.key,
      ContentLength: request.contentLength,
      ContentType: request.contentType,
      IfNoneMatch: "*",
      Metadata: { ...request.metadata },
    });
    const url = await getSignedUrl(this.#client, command, {
      expiresIn: request.expiresInSeconds,
      signableHeaders: new Set(["content-type", "if-none-match"]),
      unhoistableHeaders: new Set(
        Object.keys(request.metadata).map((key) => `x-amz-meta-${key}`),
      ),
    });
    return {
      url,
      requiredHeaders: {
        "content-type": request.contentType,
        "if-none-match": "*",
        ...Object.fromEntries(
          Object.entries(request.metadata).map(([key, value]) => [`x-amz-meta-${key}`, value]),
        ),
      },
      expiresAt: new Date(Date.now() + request.expiresInSeconds * 1000),
    };
  }

  async headObject(key: string): Promise<StoredObjectMetadata | null> {
    try {
      const response = await this.#client.send(new HeadObjectCommand({
        Bucket: this.#bucketName,
        Key: key,
      }));
      return {
        contentLength: response.ContentLength ?? -1,
        contentType: response.ContentType ?? null,
        metadata: Object.freeze({ ...(response.Metadata ?? {}) }),
      };
    } catch (error) {
      const candidate = error as { name?: string; $metadata?: { httpStatusCode?: number } };
      if (candidate.name === "NotFound" || candidate.name === "NoSuchKey" || candidate.$metadata?.httpStatusCode === 404) {
        return null;
      }
      throw error;
    }
  }

  async deleteObject(key: string): Promise<void> {
    await this.#client.send(new DeleteObjectCommand({ Bucket: this.#bucketName, Key: key }));
  }

  /** Development verification only; production STEP uploads never proxy through Next.js. */
  async getObjectBytesForVerification(key: string): Promise<Uint8Array> {
    const response = await this.#client.send(new GetObjectCommand({
      Bucket: this.#bucketName,
      Key: key,
    }));
    if (response.Body === undefined) throw new Error("R2 object body is missing");
    return response.Body.transformToByteArray();
  }
}

let storage: R2PrivateObjectStorage | undefined;

export function getPrivateObjectStorage(): R2PrivateObjectStorage {
  storage ??= new R2PrivateObjectStorage(r2ConfigurationFromEnvironment());
  return storage;
}

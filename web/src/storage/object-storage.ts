export interface PresignedPutRequest {
  readonly key: string;
  readonly contentLength: number;
  readonly contentType: string;
  readonly metadata: Readonly<Record<string, string>>;
  readonly expiresInSeconds: number;
}

export interface PresignedPut {
  readonly url: string;
  readonly requiredHeaders: Readonly<Record<string, string>>;
  readonly expiresAt: Date;
}

export interface StoredObjectMetadata {
  readonly contentLength: number;
  readonly contentType: string | null;
  readonly metadata: Readonly<Record<string, string>>;
}

/** Provider-neutral private object-storage operations needed by the V0 flow. */
export interface PrivateObjectStorage {
  createPresignedPut(request: PresignedPutRequest): Promise<PresignedPut>;
  headObject(key: string): Promise<StoredObjectMetadata | null>;
  deleteObject(key: string): Promise<void>;
}

import { useCallback, useEffect, useRef } from "react";
import type { HowToDocument } from "../../types";

export function withoutRawEvidence(document: HowToDocument): HowToDocument {
  return { ...document, source_deleted: true, source_evidence: [] };
}

/** Preserve successful document writes while discarding evidence read before cleanup. */
export function useRawEvidenceGuard() {
  const revision = useRef(0);
  useEffect(() => {
    const clear = () => { revision.current += 1; };
    window.addEventListener("minutes-raw-cleared", clear);
    return () => window.removeEventListener("minutes-raw-cleared", clear);
  }, []);
  return useCallback(async <T,>(request: Promise<T>, sanitize: (value: T) => T): Promise<T> => {
    const requestedRevision = revision.current;
    const value = await request;
    return requestedRevision === revision.current ? value : sanitize(value);
  }, []);
}

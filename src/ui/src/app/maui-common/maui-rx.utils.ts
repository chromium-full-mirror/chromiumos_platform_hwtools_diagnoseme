// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {MonoTypeOperatorFunction, map} from 'rxjs';

/** A gRPC response that reports success explicitly. */
export interface MauiSuccessResponse {
  success: boolean;
  message?: string;
}

/**
 * Turns a response with `success == false` into an error notification so that
 * callers can handle RPC errors and reported failures in one place.
 */
export function requireSuccess<T extends MauiSuccessResponse>(
  fallbackMessage: string,
): MonoTypeOperatorFunction<T> {
  return map((res: T) => {
    if (!res.success) {
      throw new Error(res.message || fallbackMessage);
    }
    return res;
  });
}

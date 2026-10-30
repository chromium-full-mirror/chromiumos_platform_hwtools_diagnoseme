// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';

/**
 * Wraps `window.location.reload()` so components can reset the station for
 * the next unit and tests can stub the reload.
 */
@Injectable({
  providedIn: 'root',
})
export class PageReloadService {
  reload(): void {
    window.location.reload();
  }
}

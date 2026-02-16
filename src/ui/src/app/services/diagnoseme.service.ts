// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {DiagnoseMeServiceClient} from '../../proto/diagnoseme.pbsc';
import {GetLogsRequest, GetLogsResponse} from '../../proto/diagnoseme.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class DiagnoseMeService {
  constructor(private client: DiagnoseMeServiceClient) {}

  getLogs(lineCount = 500): Observable<GetLogsResponse> {
    const request = new GetLogsRequest();
    request.lineCount = lineCount;
    return this.client.getLogs(request);
  }
}

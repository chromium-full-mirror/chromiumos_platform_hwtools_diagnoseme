// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Injectable } from '@angular/core';

import { ServodRpcServiceClient } from '../../proto/diagnoseme-servod.pbsc';
import { GetHardwareIDRequest, RunDutControlRequest, StartServodRequest } from 'src/proto/diagnoseme-servod.pb';

@Injectable({
  providedIn: 'root'
})
export class ServodService {

  constructor(private client: ServodRpcServiceClient) { }

  startServod(board: string, model: string, callbackfn: (started: boolean, console_output: string) => void) {
    let request: StartServodRequest = new StartServodRequest();
    request.board = board;
    request.model = model;
    this.client.startServod(request).subscribe(res => {
      callbackfn(res.started, res.consoleOutput);
    });
  }

  getHWID(
    callbackfn: (
      hwid: string,
      error: string
    ) => void
  ) {
    let request: GetHardwareIDRequest = new GetHardwareIDRequest();
    this.client.getDutHardwareId(request).subscribe(res => {
      callbackfn(res.hwid, res.consoleOutput);
    });

  }

  runDutControl(command: string, callbackfn: ( result: string ) => void) {
     let request: RunDutControlRequest = new RunDutControlRequest();
     request.command = command;
     this.client.runDutControl(request).subscribe(res => {
      callbackfn(res.result);
     })

  }
}

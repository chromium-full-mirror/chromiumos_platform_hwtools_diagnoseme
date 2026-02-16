// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component} from '@angular/core';
import {MatMenuModule} from '@angular/material/menu';
import {MatToolbarModule} from '@angular/material/toolbar';
import {MatIconModule} from '@angular/material/icon';
import {MatCardModule} from '@angular/material/card';
import {MatButtonModule} from '@angular/material/button';
import {MatTooltipModule} from '@angular/material/tooltip';
import {RouterModule} from '@angular/router';
import {DiagnoseMeService} from './services/diagnoseme.service';
import JSZip from 'jszip';
import {saveAs} from 'file-saver';

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.scss',
  imports: [
    MatMenuModule,
    MatToolbarModule,
    MatIconModule,
    MatCardModule,
    MatButtonModule,
    MatTooltipModule,
    RouterModule,
  ],
})
export class AppComponent {
  title = 'HW Tools Diagnose Me™';
  constructor(private diagnoseMeService: DiagnoseMeService) {}

  downloadLogs() {
    this.diagnoseMeService.getLogs(500).subscribe({
      next: response => {
        const zip = new JSZip();
        zip.file('rpcserver.log', response.logContent);
        zip
          .generateAsync({type: 'blob'})
          .then((content: Blob) => {
            saveAs(content, 'rpcserver_logs.zip');
          })
          .catch(err => {
            console.error('Failed to generate ZIP:', err);
            alert('Failed to generate ZIP file.');
          });
      },
      error: err => {
        console.error('Failed to download logs:', err);
        alert('Failed to download logs. See console for details.');
      },
    });
  }
}

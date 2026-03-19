// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component, OnInit} from '@angular/core';
import {HttpClient, HttpClientModule} from '@angular/common/http';
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

interface BuildInfo {
  buildDate: string;
  commitSha: string;
}

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.scss',
  imports: [
    HttpClientModule,
    MatMenuModule,
    MatToolbarModule,
    MatIconModule,
    MatCardModule,
    MatButtonModule,
    MatTooltipModule,
    RouterModule,
  ],
})
export class AppComponent implements OnInit {
  title = 'HW Tools Diagnose Me™';
  buildInfo: BuildInfo | null = null;

  constructor(
    private diagnoseMeService: DiagnoseMeService,
    private http: HttpClient,
  ) {}

  ngOnInit() {
    this.http.get<BuildInfo>('/assets/build_info.json').subscribe({
      next: data => {
        this.buildInfo = data;
      },
      error: err => {
        console.error('Failed to load build_info.json:', err);
      },
    });
  }

  downloadLogs() {
    this.diagnoseMeService.getLogs(500).subscribe({
      next: response => {
        const zip = new JSZip();
        zip.file('rpcserver.log', response.logContent);

        if (this.buildInfo) {
          const infoString = `Build Date: ${this.buildInfo.buildDate}\nCommit: ${this.buildInfo.commitSha}\n`;
          zip.file('build_info.txt', infoString);
        }

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

// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ChangeDetectorRef, Component, OnInit} from '@angular/core';
import {HttpClient, HttpClientModule} from '@angular/common/http';
import {MatMenuModule} from '@angular/material/menu';
import {MatToolbarModule} from '@angular/material/toolbar';
import {MatIconModule, MatIconRegistry} from '@angular/material/icon';
import {MatCardModule} from '@angular/material/card';
import {MatButtonModule} from '@angular/material/button';
import {MatDividerModule} from '@angular/material/divider';
import {MatTooltipModule} from '@angular/material/tooltip';
import {RouterModule} from '@angular/router';
import {DiagnoseMeService} from './services/diagnoseme.service';
import {ACTIVE_EDITION} from './editions/active-edition';
import {Edition} from './editions/edition.model';
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
    MatDividerModule,
    MatTooltipModule,
    RouterModule,
  ],
})
export class AppComponent implements OnInit {
  title = 'HW Tools Diagnose Me™';
  buildInfo: BuildInfo | null = null;
  /** Edition compiled into this UI build. */
  readonly edition: Edition = ACTIVE_EDITION;
  /** Edition reported by the backend, when it differs from the UI one. */
  serverEditionMismatch: string | null = null;

  constructor(
    private diagnoseMeService: DiagnoseMeService,
    private http: HttpClient,
    private cdr: ChangeDetectorRef,
    iconRegistry: MatIconRegistry,
  ) {
    // Pages use Material Symbols ligatures (was set in the former AppModule).
    iconRegistry.setDefaultFontSetClass('material-symbols-outlined');
  }

  ngOnInit() {
    this.http.get<BuildInfo>('/assets/build_info.json').subscribe({
      next: data => {
        this.buildInfo = data;
        // The app is zoneless: RPC / HTTP replies do not redraw by themselves.
        this.cdr.markForCheck();
      },
      error: err => {
        console.error('Failed to load build_info.json:', err);
      },
    });
    this.diagnoseMeService.getStationConfig().subscribe({
      next: config => {
        // The 'all' edition is a development build working with any backend.
        if (
          this.edition.id !== 'all' &&
          config.edition &&
          config.edition !== this.edition.id
        ) {
          this.serverEditionMismatch = config.edition;
          this.cdr.markForCheck();
        }
      },
      error: err => {
        console.error('Failed to get station config:', err);
      },
    });
  }

  downloadLogs() {
    this.diagnoseMeService.getLogs(500).subscribe({
      next: response => {
        const zip = new JSZip();
        zip.file('rpcserver.log', response.logContent);

        const buildDate = this.buildInfo?.buildDate ?? 'unknown';
        const commitSha = this.buildInfo?.commitSha ?? 'unknown';
        const infoString =
          `Build Date: ${buildDate}\nCommit: ${commitSha}\n` +
          `Edition: ${this.edition.id}\n`;
        zip.file('build_info.txt', infoString);

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

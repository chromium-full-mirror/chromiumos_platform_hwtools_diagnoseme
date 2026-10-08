// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {CommonModule} from '@angular/common';
import {ChangeDetectorRef, Component, ViewChild} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatCardModule} from '@angular/material/card';
import {MatDialog} from '@angular/material/dialog';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {MatSelectModule} from '@angular/material/select';
import {MatToolbarModule} from '@angular/material/toolbar';
import {NgxMatSelectSearchModule} from 'ngx-mat-select-search';

import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {
  DialogFirmwareComplete,
  DialogOops,
} from '../dolos-provision/dolos-provision';
import {HelpDialogButton} from '../help-dialog-button/help-dialog-button';
import {DolosService} from '../services/dolos.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-dolos-cable-programming',
  standalone: true,
  templateUrl: './dolos-cable-programming.html',
  styleUrl: './dolos-cable-programming.scss',
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatSelectModule,
    MatToolbarModule,
    NgxMatSelectSearchModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    HelpDialogButton,
    TestPassFailButton,
  ],
})
export class DolosCableProgrammingComponent {
  @ViewChild('programCableStatus')
  programCableStatus!: TestPassFailButton;

  models: string[] = [];
  filteredModels: string[] = [];
  selectedModel = '';
  manualHwid = '';
  eepromData = '';
  selectedFileName = '';
  cableProgramProgress = false;

  constructor(
    public dialog: MatDialog,
    private dolosService: DolosService,
    private cdr: ChangeDetectorRef,
  ) {
    this.dolosService.getModels(this.updateModels);
  }

  updateModels = (models: string[]) => {
    this.models = [...models].sort((a, b) => (a > b ? 1 : b > a ? -1 : 0));
    this.filteredModels = this.models;
    this.cdr.detectChanges();
  };

  filterModels(filterValue: string) {
    filterValue = filterValue.trim().toLowerCase();
    this.filteredModels = this.models.filter(model =>
      model.toLowerCase().startsWith(filterValue),
    );
  }

  onChangeModel(newValue: string) {
    this.selectedModel = newValue || '';
    this.programCableStatus?.hide();
  }

  onManualHwidChange() {
    this.programCableStatus?.hide();
  }

  onEepromDataFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      const selectedFile = input.files[0];
      this.selectedFileName = selectedFile.name;
      this.programCableStatus?.hide();
      const reader = new FileReader();
      reader.onload = (e: ProgressEvent<FileReader>) => {
        if (e.target && e.target.result) {
          this.eepromData = e.target.result as string;
          this.cdr.detectChanges();
        }
      };
      reader.onerror = () => {
        console.error('Error reading file:', reader.error);
        this.eepromData = '';
        this.selectedFileName = '';
        this.cdr.detectChanges();
      };
      reader.readAsText(selectedFile);
    }
  }

  clearEepromDataFile(fileInput?: HTMLInputElement): void {
    this.eepromData = '';
    this.selectedFileName = '';
    this.programCableStatus?.hide();
    if (fileInput) {
      fileInput.value = '';
    }
  }

  getEffectiveHwid(): string {
    const trimmedManualHwid = this.manualHwid.trim();
    return trimmedManualHwid !== '' ? trimmedManualHwid : this.selectedModel;
  }

  canProgramCable(): boolean {
    return (
      !this.cableProgramProgress &&
      (this.selectedModel !== '' ||
        this.manualHwid.trim() !== '' ||
        this.eepromData !== '')
    );
  }

  onCableProgram(): void {
    this.cableProgramProgress = true;
    this.programCableStatus?.showWorking();
    this.cdr.detectChanges();
    const effectiveHwid = this.getEffectiveHwid();
    void this.dolosService.programCable(
      effectiveHwid,
      this.eepromData,
      this.afterProgramCable,
    );
  }

  afterProgramCable = (success: boolean, errorMessage: string) => {
    this.cableProgramProgress = false;
    if (success) {
      this.programCableStatus?.showPassed();
    } else {
      this.programCableStatus?.showFailed();
    }
    this.cdr.detectChanges();

    if (!success && errorMessage) {
      this.dialog.open(DialogOops, {
        data: errorMessage,
      });
      return;
    }

    this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Cable Program',
        success: success ? 1 : 0,
        failed: success ? 0 : 1,
      },
    });
  };

  onReload(): void {
    location.reload();
  }
}

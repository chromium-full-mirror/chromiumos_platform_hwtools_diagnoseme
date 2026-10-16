// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {RouterTestingModule} from '@angular/router/testing';
import {HttpClientTestingModule} from '@angular/common/http/testing';
import {AppComponent} from './app';
import {DiagnoseMeService} from './services/diagnoseme.service';
import {StationConfig} from '../proto/diagnoseme.pb';
import {of} from 'rxjs';

describe('AppComponent', () => {
  let mockDiagnoseMeService: jasmine.SpyObj<DiagnoseMeService>;

  beforeEach(async () => {
    mockDiagnoseMeService = jasmine.createSpyObj('DiagnoseMeService', [
      'getLogs',
      'getStationConfig',
    ]);
    mockDiagnoseMeService.getStationConfig.and.returnValue(
      of(new StationConfig({edition: 'all'})),
    );
    await TestBed.configureTestingModule({
      imports: [RouterTestingModule, HttpClientTestingModule, AppComponent],
      providers: [
        {provide: DiagnoseMeService, useValue: mockDiagnoseMeService},
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it("should have as title 'HW Tools Diagnose Me™'", () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app.title).toEqual('HW Tools Diagnose Me™');
  });

  it('should render title', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(
      compiled.querySelector('.diagnoseme-toolbar')?.textContent,
    ).toContain('HW Tools Diagnose Me');
  });

  it('should render the menu of the active edition', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app.edition.menu.length).toBeGreaterThan(0);
    expect(app.edition.routes.length).toBeGreaterThan(0);
  });

  it('should not flag a mismatch for the development edition', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.serverEditionMismatch).toBeNull();
  });
});

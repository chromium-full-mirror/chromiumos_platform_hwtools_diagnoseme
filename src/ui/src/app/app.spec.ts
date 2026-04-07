// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {RouterTestingModule} from '@angular/router/testing';
import {HttpClientTestingModule} from '@angular/common/http/testing';
import {AppComponent} from './app';
import {DiagnoseMeService} from './services/diagnoseme.service';

describe('AppComponent', () => {
  let mockDiagnoseMeService: jasmine.SpyObj<DiagnoseMeService>;

  beforeEach(async () => {
    mockDiagnoseMeService = jasmine.createSpyObj('DiagnoseMeService', [
      'getLogs',
    ]);
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
});

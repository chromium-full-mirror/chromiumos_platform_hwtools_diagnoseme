// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MauiStationFooterComponent} from './maui-station-footer';
import {provideRouter} from '@angular/router';

describe('MauiStationFooterComponent', () => {
  let component: MauiStationFooterComponent;
  let fixture: ComponentFixture<MauiStationFooterComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiStationFooterComponent],
      providers: [provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiStationFooterComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});

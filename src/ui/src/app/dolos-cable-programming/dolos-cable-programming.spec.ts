// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';

import {DolosService} from '../services/dolos.service';
import {DolosCableProgrammingComponent} from './dolos-cable-programming';

class MockDolosService {
  getModels = jasmine
    .createSpy('getModels')
    .and.callFake((callback: (models: string[]) => void) => {
      callback(['ocelot', 'brya', 'akali']);
    });

  programCable = jasmine
    .createSpy('programCable')
    .and.callFake(
      (
        _hwid: string,
        _eepromData: string,
        callback: (success: boolean, errorMessage: string) => void,
      ) => {
        callback(true, '');
      },
    );
}

describe('DolosCableProgrammingComponent', () => {
  let component: DolosCableProgrammingComponent;
  let fixture: ComponentFixture<DolosCableProgrammingComponent>;
  let mockDolosService: MockDolosService;

  beforeEach(async () => {
    mockDolosService = new MockDolosService();

    await TestBed.configureTestingModule({
      imports: [DolosCableProgrammingComponent, NoopAnimationsModule],
      providers: [{provide: DolosService, useValue: mockDolosService}],
    }).compileComponents();

    fixture = TestBed.createComponent(DolosCableProgrammingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load sorted models', () => {
    expect(component).toBeTruthy();
    expect(component.models).toEqual(['akali', 'brya', 'ocelot']);
    expect(component.filteredModels).toEqual(['akali', 'brya', 'ocelot']);
  });

  it('should filter models by prefix', () => {
    component.filterModels('br');
    expect(component.filteredModels).toEqual(['brya']);
  });

  it('should disable program cable when no input is provided', () => {
    expect(component.canProgramCable()).toBeFalse();
  });

  it('should program cable using selected model when manual HWID is empty', () => {
    spyOn(component.dialog, 'open');
    spyOn(component.programCableStatus, 'showPassed').and.callThrough();
    component.onChangeModel('ocelot');
    expect(component.canProgramCable()).toBeTrue();

    component.onCableProgram();
    expect(mockDolosService.programCable).toHaveBeenCalledWith(
      'ocelot',
      '',
      jasmine.any(Function),
    );
    expect(component.cableProgramProgress).toBeFalse();
    expect(component.programCableStatus.showPassed).toHaveBeenCalled();
    expect(component.dialog.open).toHaveBeenCalled();
  });

  it('should prefer manual HWID over selected model when provided', () => {
    spyOn(component.dialog, 'open');
    component.onChangeModel('brya');
    component.manualHwid = '  OCELOT-ABCD D2B-A2A  ';
    expect(component.getEffectiveHwid()).toEqual('OCELOT-ABCD D2B-A2A');

    component.onCableProgram();
    expect(mockDolosService.programCable).toHaveBeenCalledWith(
      'OCELOT-ABCD D2B-A2A',
      '',
      jasmine.any(Function),
    );
  });

  it('should allow programming with uploaded eepromData and clear it', () => {
    component.eepromData = 'custom: yaml';
    component.selectedFileName = 'custom.yaml';
    expect(component.canProgramCable()).toBeTrue();

    component.clearEepromDataFile();
    expect(component.eepromData).toEqual('');
    expect(component.selectedFileName).toEqual('');
    expect(component.canProgramCable()).toBeFalse();
  });
});

// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {of} from 'rxjs';

import {DolosProvisionComponent} from './dolos-provision';
import {DolosService} from '../services/dolos.service';
import {ServodService} from '../services/servod-service';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {
  FirmwareVersionsResponse,
  ModelsResponse,
  BoardModelMappingResponse,
  FirmwareUpdateResponse,
  CheckDolosFromHostResponse,
  ProgramCableResponse,
} from '../../proto/diagnoseme-dolos.pb';
import {
  StartServodResponse,
  GetHardwareIDResponse,
  RunDutControlResponse,
} from '../../proto/diagnoseme-servod.pb';

// Mock Services
class MockDolosService {
  getFirmwareVersions() {
    return of(new FirmwareVersionsResponse());
  }
  getModels() {
    return of(new ModelsResponse());
  }
  getBoardModelMapping() {
    return of(new BoardModelMappingResponse());
  }
  updateFirmware() {
    return of(new FirmwareUpdateResponse());
  }
  checkDolosFromHost() {
    return of(new CheckDolosFromHostResponse());
  }
  programCable() {
    return of(new ProgramCableResponse());
  }
}

class MockServodService {
  startServod() {
    return of(new StartServodResponse());
  }
  getDutHardwareId() {
    return of(new GetHardwareIDResponse());
  }
  runDutControl() {
    return of(new RunDutControlResponse());
  }
}

describe('DolosProvisionComponent', () => {
  let component: DolosProvisionComponent;
  let fixture: ComponentFixture<DolosProvisionComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DolosProvisionComponent, NoopAnimationsModule],
      providers: [
        {provide: DolosService, useClass: MockDolosService},
        {provide: ServodService, useClass: MockServodService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(DolosProvisionComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});

// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {NgModule} from '@angular/core';

import {ModuleWithProviders} from '@angular/core';
import {GRPC_CLIENT_FACTORY, GrpcCoreModule} from '@ngx-grpc/core';
import {
  GrpcWebClientFactory,
  GrpcWebClientSettings,
} from '@ngx-grpc/grpc-web-client';
import {GRPC_DOLOS_RPC_SERVICE_CLIENT_SETTINGS} from '../../proto/diagnoseme-dolos.pbconf';
import {GRPC_SERVOD_RPC_SERVICE_CLIENT_SETTINGS} from '../../proto/diagnoseme-servod.pbconf';
import {
  GRPC_SERVO_V41_MANUFACTURING_CLIENT_SETTINGS,
  GRPC_SERVO_MICRO_MANUFACTURING_CLIENT_SETTINGS,
} from '../../proto/servo-manufacturing.pbconf';
import {GRPC_DIAGNOSE_ME_SERVICE_CLIENT_SETTINGS} from '../../proto/diagnoseme.pbconf';
import {
  GRPC_MAUI_STATION1_INITIAL_FLASHING_CLIENT_SETTINGS,
  GRPC_MAUI_PDC_FLASHING_CLIENT_SETTINGS,
  GRPC_MAUI_STATION2_FUNCTIONAL_TESTING_CLIENT_SETTINGS,
  GRPC_MAUI_DEVICE_MANAGEMENT_CLIENT_SETTINGS,
  GRPC_MAUI_MCU_SERVICE_CLIENT_SETTINGS,
} from '../../proto/maui-manufacturing.pbconf';

@NgModule({
  imports: [GrpcCoreModule.forRoot()],
})
export class ServicesModule {
  static getServiceURL(): string {
    const url = new URL(window.location.href);
    return `${url.protocol}//${url.hostname}:${url.port}/rpc`;
  }

  static forRoot(): ModuleWithProviders<ServicesModule> {
    return {
      ngModule: ServicesModule,
      providers: [
        {
          provide: GRPC_DOLOS_RPC_SERVICE_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_SERVOD_RPC_SERVICE_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_SERVO_V41_MANUFACTURING_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_SERVO_MICRO_MANUFACTURING_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_DIAGNOSE_ME_SERVICE_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_MAUI_STATION1_INITIAL_FLASHING_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_MAUI_PDC_FLASHING_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_MAUI_STATION2_FUNCTIONAL_TESTING_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_MAUI_DEVICE_MANAGEMENT_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {
          provide: GRPC_MAUI_MCU_SERVICE_CLIENT_SETTINGS,
          useValue: {host: this.getServiceURL()} as GrpcWebClientSettings,
        },
        {provide: GRPC_CLIENT_FACTORY, useClass: GrpcWebClientFactory},
      ],
    };
  }
}

export {};

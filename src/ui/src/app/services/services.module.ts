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

@NgModule({
  imports: [GrpcCoreModule.forRoot()],
})
export class ServicesModule {
  static getServiceURL() {
    const url = new URL(window.location.href);
    const hostname = url.hostname;
    const port = url.port;
    let service_url = new String(url.protocol);

    service_url = service_url.concat('//', hostname, ':', port);
    service_url = service_url.concat('/rpc');
    console.log(service_url);

    return service_url;
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
        {provide: GRPC_CLIENT_FACTORY, useClass: GrpcWebClientFactory},
      ],
    };
  }
}

export {};

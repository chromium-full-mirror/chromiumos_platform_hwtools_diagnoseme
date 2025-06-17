// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { platformBrowser } from '@angular/platform-browser';

import { AppModule } from './app/app.module';


platformBrowser().bootstrapModule(AppModule)
  .catch(err => console.error(err));

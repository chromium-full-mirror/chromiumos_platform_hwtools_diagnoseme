// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// The edition compiled into this build.
//
// This file is replaced at build time by active-edition.<id>.ts through the
// Angular 'fileReplacements' of the matching angular.json configuration, e.g.
// `ng build --configuration=production,maui-v1.1`. The default is the
// development edition containing every page.
export {EDITION as ACTIVE_EDITION} from './all.edition';

import { Injectable } from '@angular/core';

import { ServodRpcServiceClient } from '../../proto/diagnoseme-servod.pbsc';
import { GetHardwareIDRequest } from 'src/proto/diagnoseme-servod.pb';

@Injectable({
  providedIn: 'root'
})
export class ServodService {
  constructor(private client: ServodRpcServiceClient) {}

  getHWID(
    callbackfn: (
      hwid: string,
      error: string
    ) => void
  ) {
      let request: GetHardwareIDRequest = new GetHardwareIDRequest();
      this.client.getDutHardwareId(request).subscribe(res => {
        console.log(res.hwid)
      });

    }
}

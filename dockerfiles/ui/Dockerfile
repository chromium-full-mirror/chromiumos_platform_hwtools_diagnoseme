# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################
FROM node:25 AS build

WORKDIR /app

# add `/app/node_modules/.bin` to $PATH
ENV PATH=/app/node_modules/.bin:$PATH


# install and cache app dependencies
# add app
COPY src/ui/package.json /app

RUN npm install -g npm@latest
RUN npm install --ignore-scripts --legacy-peer-deps

# install and cache app dependencies
# add app

COPY src/ui/ /app/

WORKDIR /app
RUN ng build --output-path=dist --output-hashing=all --configuration=production

#####################################################################

FROM nginx:alpine

RUN mkdir -p /var/cache/nginx/client_temp
RUN mkdir -p /var/cache/nginx/proxy_temp
RUN chmod uog+rw /var/run

# copy artifact build from the 'build environment'
COPY --from=build /app/dist/browser /etc/nginx/html/

COPY src/ui/nginx.conf /etc/nginx
RUN rm -f /etc/nginx/conf.d/*
RUN rm -f /docker-entrypoint.d/*
RUN rm -f /var/log/nginx/*
RUN chmod uog+rw /var/log/nginx

COPY src/ui/src/assets/* /etc/nginx/html/assets/img/
COPY src/ui/src/assets/build_info.json /etc/nginx/html/assets/



EXPOSE 6010

#USER diagnoseme

# run nginx
CMD ["nginx", "-g", "daemon off;"]

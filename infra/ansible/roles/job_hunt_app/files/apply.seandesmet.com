server {
  server_name apply.seandesmet.com;
  # Resume uploads (0.2) are PDFs and DOCX files; Nginx's default is 1 MB.
  client_max_body_size 10m;
  location / {
    proxy_pass http://127.0.0.1:5004;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/apply.seandesmet.com/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/apply.seandesmet.com/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

}
server {
    if ($host = apply.seandesmet.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


  server_name apply.seandesmet.com;
  listen 80;
    return 404; # managed by Certbot


}
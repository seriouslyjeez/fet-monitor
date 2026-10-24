// Parsing of an HTTP/1.1 response that was read until the server closed the connection.
#pragma once
#include <string>

namespace fet {

// True only for a complete, well-formed response: status line, headers, and (when Content-Length is given) the whole body.
// A reply that was cut short is NOT an answer: treating it as one could acknowledge events the server never stored.
bool parseHttpResponse(const std::string& raw, int& status, std::string& body);

}  // namespace fet

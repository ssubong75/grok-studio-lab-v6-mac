#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
  char executable_path[PATH_MAX];
  uint32_t path_size = sizeof(executable_path);

  if (_NSGetExecutablePath(executable_path, &path_size) != 0) {
    return 1;
  }

  char resolved_path[PATH_MAX];
  if (realpath(executable_path, resolved_path) == NULL) {
    return 1;
  }

  char directory_path[PATH_MAX];
  snprintf(directory_path, sizeof(directory_path), "%s", resolved_path);
  char *macos_directory = dirname(directory_path);

  char launcher_path[PATH_MAX];
  snprintf(
      launcher_path,
      sizeof(launcher_path),
      "%s/../Resources/launch_grok_studio.zsh",
      macos_directory);

  execl("/bin/zsh", "zsh", launcher_path, (char *)NULL);
  return 1;
}

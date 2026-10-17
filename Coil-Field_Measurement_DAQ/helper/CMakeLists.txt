cmake_minimum_required(VERSION 3.18)
project(dscope_helper C)

# ---------------------------------------------------------------------------
# DSView source (for libsigrok4DSL). Either point DSVIEW_DIR at a checkout,
# or let CMake download the pinned version below.
# ---------------------------------------------------------------------------
set(DSVIEW_DIR "" CACHE PATH "Path to a DSView source checkout")
set(DSVIEW_GIT_TAG 2e9e2c8e726df4ef5687d39b83d4f797cc44b574 CACHE STRING
    "DSView commit to download when DSVIEW_DIR is not set")

if(NOT DSVIEW_DIR)
  include(FetchContent)
  FetchContent_Declare(dsview
    GIT_REPOSITORY https://github.com/DreamSourceLab/DSView.git
    GIT_TAG ${DSVIEW_GIT_TAG}
    # only download; don't build DSView itself (no CMakeLists in this dir)
    SOURCE_SUBDIR _download_only)
  FetchContent_MakeAvailable(dsview)
  set(DSVIEW_DIR ${dsview_SOURCE_DIR})
endif()
message(STATUS "Using DSView source: ${DSVIEW_DIR}")

find_package(PkgConfig REQUIRED)
pkg_check_modules(GLIB REQUIRED glib-2.0)
pkg_check_modules(LIBUSB REQUIRED libusb-1.0)
pkg_check_modules(ZLIB REQUIRED zlib)
find_package(Threads REQUIRED)

set(L ${DSVIEW_DIR}/libsigrok4DSL)
set(C ${DSVIEW_DIR}/common)

add_executable(dscope_helper
  dscope_helper.c
  ${L}/version.c ${L}/strutil.c ${L}/std.c ${L}/session_driver.c
  ${L}/session.c ${L}/log.c ${L}/hwdriver.c ${L}/error.c ${L}/backend.c
  ${L}/output/output.c ${L}/input/input.c ${L}/hardware/demo/demo.c
  ${L}/input/in_binary.c ${L}/input/in_vcd.c ${L}/input/in_wav.c
  ${L}/output/csv.c ${L}/output/gnuplot.c ${L}/output/srzip.c
  ${L}/output/vcd.c ${L}/hardware/DSL/dslogic.c
  ${L}/hardware/common/usb.c ${L}/hardware/common/ezusb.c
  ${L}/trigger.c ${L}/dsdevice.c ${L}/hardware/DSL/dscope.c
  ${L}/hardware/DSL/command.c ${L}/hardware/DSL/dsl.c ${L}/lib_main.c
  ${C}/minizip/zip.c ${C}/minizip/unzip.c ${C}/minizip/ioapi.c
  ${C}/log/xlog.c)

# libsigrok4DSL includes <libusb-1.0/libusb.h>, so also add the parent dir
set(USB_PARENT_DIRS "")
foreach(d ${LIBUSB_INCLUDE_DIRS})
  get_filename_component(p ${d} DIRECTORY)
  list(APPEND USB_PARENT_DIRS ${p})
endforeach()

target_include_directories(dscope_helper PRIVATE
  ${L} ${C} ${DSVIEW_DIR}
  ${GLIB_INCLUDE_DIRS} ${LIBUSB_INCLUDE_DIRS} ${USB_PARENT_DIRS}
  ${ZLIB_INCLUDE_DIRS})
target_link_directories(dscope_helper PRIVATE
  ${GLIB_LIBRARY_DIRS} ${LIBUSB_LIBRARY_DIRS} ${ZLIB_LIBRARY_DIRS})
target_link_libraries(dscope_helper PRIVATE
  ${GLIB_LIBRARIES} ${LIBUSB_LIBRARIES} ${ZLIB_LIBRARIES}
  Threads::Threads m)
target_compile_definitions(dscope_helper PRIVATE _GNU_SOURCE _DARWIN_C_SOURCE)
set_property(TARGET dscope_helper PROPERTY C_STANDARD 99)
if(CMAKE_C_COMPILER_ID MATCHES "Clang")
  # the library has a few warnings that newer clang treats as errors
  target_compile_options(dscope_helper PRIVATE
    -Wno-implicit-function-declaration -Wno-int-conversion
    -Wno-incompatible-pointer-types)
endif()

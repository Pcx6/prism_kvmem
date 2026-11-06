llama_add_compile_flags()

#
# libraries
#

# llama

file(GLOB LLAMA_MODELS_SOURCES "models/*.cpp")

add_library(llama
            ../include/llama.h
            llama.cpp
            llama-adapter.cpp
            llama-arch.cpp
            llama-batch.cpp
            llama-chat.cpp
            llama-context.cpp
            llama-cparams.cpp
            llama-grammar.cpp
            llama-graph.cpp
            llama-hparams.cpp
            llama-impl.cpp
            llama-io.cpp
            llama-kv-cache.cpp
            llama-kv-cache-iswa.cpp
            llama-kv-cache-dsa.cpp
            llama-kv-cache-dsa-iswa.cpp
            llama-kv-cache-msa.cpp
            llama-kv-cache-dsv4.cpp
            llama-memory.cpp
            llama-memory-hybrid.cpp
            llama-memory-hybrid-iswa.cpp
            llama-memory-recurrent.cpp
            llama-mmap.cpp
            llama-model-loader.cpp
            llama-model-saver.cpp
            llama-model.cpp
            llama-quant.cpp
            llama-sampler.cpp
            llama-vocab.cpp
            unicode-data.cpp
            unicode.cpp
            unicode.h
            ${LLAMA_MODELS_SOURCES}
            )

set_target_properties(llama PROPERTIES
    VERSION ${LLAMA_VERSION_BASE}
    SOVERSION ${LLAMA_VERSION_MAJOR}
    MACHO_CURRENT_VERSION 0 # keep macOS linker from seeing oversized version number
)

target_compile_definitions(llama PRIVATE
    LLAMA_VERSION="${LLAMA_VERSION}"
    LLAMA_COMMIT="${LLAMA_BUILD_COMMIT}"
)

target_include_directories(llama PRIVATE .)
target_include_directories(llama PUBLIC ../include)
target_compile_features   (llama PRIVATE cxx_std_17) # don't bump

target_link_libraries(llama PUBLIC ggml)

if (LLAMA_KVMEM)
    if (LLAMA_KVMEM_ROOT STREQUAL "")
        message(FATAL_ERROR "LLAMA_KVMEM=ON requires LLAMA_KVMEM_ROOT")
    endif()
    target_sources(llama PRIVATE
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-memory-kvmem.cpp
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-attention.cpp
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-memory-kvmem-hybrid.cpp
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-memory-kvmem-mtp.cpp
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-batch.cpp
        ${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-capture.cpp
    )
    # adapter sources come from a newer ggml API; this header adds the missing generic setter
    set_source_files_properties(${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-attention.cpp
        PROPERTIES COMPILE_OPTIONS "-include;${CMAKE_CURRENT_SOURCE_DIR}/kvmem-compat.h")
    if (GGML_CUDA)
        enable_language(CUDA)
        target_sources(llama PRIVATE
            ${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-attention-cuda.cu
            ${LLAMA_KVMEM_ROOT}/src/adapter/llama-kvmem-stagein.cu)
        if (DEFINED CMAKE_CUDA_ARCHITECTURES)
            set_property(TARGET llama PROPERTY CUDA_ARCHITECTURES ${CMAKE_CUDA_ARCHITECTURES})
        endif()
    endif()
    target_include_directories(llama PRIVATE
        ${LLAMA_KVMEM_ROOT}/kvmem/include
        ${LLAMA_KVMEM_ROOT}/src/adapter
        ${CMAKE_CURRENT_SOURCE_DIR}/../ggml/src
    )
    target_compile_definitions(llama PRIVATE LLAMA_KVMEM=1)
    if (NOT TARGET kvmem)
        add_subdirectory(${LLAMA_KVMEM_ROOT}/kvmem ${CMAKE_BINARY_DIR}/kvmem-from-llama)
    endif()
    target_link_libraries(llama PRIVATE kvmem)
    if (GGML_CUDA)
        find_package(CUDAToolkit QUIET)
        if (CUDAToolkit_FOUND)
            # The CPU page-softmax scorer is linear in blocks x query rows and dominates
            # 256K selection. Build the CUDA variant the standalone KVMem server uses.
            if (NOT TARGET kvmem-recall-cuda)
                add_library(kvmem-recall-cuda OBJECT ${LLAMA_KVMEM_ROOT}/kvmem/src/cuda/page_softmax.cu)
                target_include_directories(kvmem-recall-cuda PRIVATE ${LLAMA_KVMEM_ROOT}/kvmem/include)
                set_target_properties(kvmem-recall-cuda PROPERTIES
                    CUDA_STANDARD 17 CUDA_STANDARD_REQUIRED ON POSITION_INDEPENDENT_CODE ON)
                if (DEFINED CMAKE_CUDA_ARCHITECTURES)
                    set_property(TARGET kvmem-recall-cuda PROPERTY CUDA_ARCHITECTURES ${CMAKE_CUDA_ARCHITECTURES})
                endif()
                target_sources(kvmem PRIVATE $<TARGET_OBJECTS:kvmem-recall-cuda>)
                target_compile_definitions(kvmem PUBLIC KVMEM_PAGE_SOFTMAX_CUDA=1)
                target_link_libraries(kvmem PUBLIC CUDA::cudart)
            endif()
            target_link_libraries(llama PRIVATE CUDA::cudart)
        endif()
    endif()
endif()

if (BUILD_SHARED_LIBS)
    set_target_properties(llama PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_compile_definitions(llama PRIVATE LLAMA_BUILD)
    target_compile_definitions(llama PUBLIC  LLAMA_SHARED)
endif()

// One file owns one decision: consent must never spill over to the next file.
export async function uploadWithReview(file, upload, review) {
  try {
    return await upload(false);
  } catch (error) {
    if (error.code !== "similar_document" && error.code !== "duplicate") throw error;
    const exact = error.code === "duplicate";
    const accepted = await review({
      file: file.name, exact,
      existing: error.data?.duplicate,
      duplicates: error.data?.duplicates,
    });
    if (exact || !accepted) return null;
    return upload(true);
  }
}

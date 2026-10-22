# Importing Records

An import describes the complete set of Records you want in a Dataset; it does not simply append rows. Refrncr compares the file with the Draft's current Records and marks each one as Added, Changed, Unchanged or Deleted. Review the preview and confirm the deletion count before importing.

For a Dataset that has never been published (no Scheduled, Current or Expired version), you can import after adding, editing or deleting Records by hand, and you can import again after an earlier import. Each preview compares the file with the Records currently in the Draft. The **Import results** filter on the Records tab shows the results of the latest import only.

After a Dataset has a version, imports into a Draft are restricted to protect its existing content: once Records in that Draft have changed or an import has completed, another import is unavailable. Publish or discard the Draft before starting a new import. Older Drafts whose Record history cannot be verified also remain unavailable for import.

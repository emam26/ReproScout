import { repositoryUrl } from "./content";
export function PublicFooter() {
  return (
    <footer>
      <div className="wrap">
        <span>ReproScout Â· MIT License</span>
        <ul>
          <li><a href={repositoryUrl} rel="noreferrer" target="_blank">GitHub</a></li>
          <li><a href={`${repositoryUrl}/blob/main/CONTRIBUTING.md`} rel="noreferrer" target="_blank">Contributing</a></li>
          <li><a href={`${repositoryUrl}/blob/main/LICENSE`} rel="noreferrer" target="_blank">License</a></li>
        </ul>
      </div>
    </footer>
  );
}
